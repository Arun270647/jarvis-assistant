"""Chatterbox-Turbo voice for Jarvis (tts.engine = "chatterbox").

- The model runs in its own Python (tts.chatterbox.python: a separate environment with
  chatterbox-tts and torch) through src/tts/chatterbox_worker.py, started hidden and loaded once.
- Replies are voiced sentence by sentence: every sentence is queued at once, and each
  plays as soon as it is ready while the worker generates the next.
- Fixed phrases (tts.chatterbox.cache_phrases) are generated once and kept in
  tts/chatterbox in the cache folder, so they play instantly; prepare() does the same for a one-off text
  (the next window greeting) and discard() deletes it after use.
- Piper speaks while the worker is still loading, or if it fails or times out
  (tts.chatterbox.fallback = "piper").
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from src.core import paths, winproc

WORKER = Path(__file__).resolve().with_name("chatterbox_worker.py")
CACHE_DIR = paths.cache_dir() / "tts" / "chatterbox"
TMP_DIR = paths.cache_dir() / "tts" / "tmp"


def split_sentences(text: str) -> list:
    """Sentences to voice one at a time (long ones split again at commas so none runs past ~40 s)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    out = []
    for p in parts:
        while len(p) > 280:
            cut = p.rfind(", ", 0, 280)
            cut = cut if cut > 80 else p.rfind(" ", 0, 280)
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        out.append(p)
    return out


def read_wav(path) -> tuple:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy(), w.getframerate()


class ChatterboxTTS:
    voice_name = "chatterbox-turbo"

    def __init__(self, config: dict, fallback=None):
        tts_cfg = config.get("tts", {})
        self.cfg = tts_cfg.get("chatterbox", {}) or {}
        self.fallback = fallback
        self._sr = 24000
        self._proc = None
        self._ready = threading.Event()
        self._failed: Optional[str] = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._results = {}                       # id -> result dict
        self._cv = threading.Condition()
        self._stop = threading.Event()
        from src.core.config_store import assistant_name, with_name
        name = assistant_name()
        self._phrases = [with_name(p, name).strip() for p in self.cfg.get("cache_phrases", []) if p and p.strip()]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._start, name="chatterbox-start", daemon=True).start()

    # ------------------------------------------------------------------ worker

    def _start(self):
        python = self.cfg.get("python", "")
        if not python or not Path(python).exists():
            return self._fail(f"Chatterbox python not found: {python!r} (tts.chatterbox.python)")
        env = dict(os.environ)
        for key, value in (("HF_HOME", self.cfg.get("hf_home")), ("TORCH_HOME", self.cfg.get("torch_home"))):
            if value:
                env[key] = value
        env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "TQDM_DISABLE": "1",
                    "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "TMP": str(TMP_DIR), "TEMP": str(TMP_DIR),
                    "JARVIS_TTS_DEVICE": self.cfg.get("device", "cpu"),
                    "JARVIS_TTS_TEMPERATURE": str(self.cfg.get("temperature", 0.8)),
                    "JARVIS_TTS_THREADS": str(self.cfg.get("threads", 0))})
        if self.cfg.get("device", "cpu") == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""     # keep the GPU for Whisper
        t0 = time.time()
        try:
            import subprocess
            self._proc = winproc.popen_hidden([python, "-I", str(WORKER)], stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                              text=True, encoding="utf-8", bufsize=1)
        except Exception as e:
            return self._fail(f"Could not start the Chatterbox worker: {e}")
        threading.Thread(target=self._read, name="chatterbox-read", daemon=True).start()
        timeout = float(self.cfg.get("startup_timeout_s", 600))
        if not self._ready.wait(timeout) and not self._failed:
            self._fail(f"Chatterbox did not load within {timeout:.0f} s")
            self.close()
            return
        if self._failed:
            return
        print(f"[TTS] Chatterbox-Turbo ready ({time.time() - t0:.1f}s, {self._sr}Hz, {self.cfg.get('device', 'cpu')})")
        self._warm_cache()

    def _fail(self, why: str):
        self._failed = why
        print(f"[TTS] {why}" + (" — using Piper" if self.fallback else ""))
        with self._cv:
            self._cv.notify_all()

    def _read(self):
        for line in self._proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ready" in msg:
                if msg["ready"]:
                    self._sr = int(msg.get("sr", 24000))
                    self._ready.set()
                else:
                    self._fail(f"Chatterbox failed to load: {msg.get('error')}")
                continue
            with self._cv:
                self._results[msg.get("id")] = msg
                self._cv.notify_all()
        if not self._failed:
            self._fail("Chatterbox worker exited")
        self._ready.clear()

    def _submit(self, text: str) -> int:
        with self._lock:
            self._next_id += 1
            jid = self._next_id
            out = TMP_DIR / f"say-{os.getpid()}-{jid}.wav"
            self._proc.stdin.write(json.dumps({"op": "say", "id": jid, "text": text, "out": str(out)}) + "\n")
            self._proc.stdin.flush()
        return jid

    def _wait(self, jid: int, timeout: float) -> Optional[dict]:
        end = time.time() + timeout
        with self._cv:
            while jid not in self._results:
                left = end - time.time()
                if left <= 0 or self._stop.is_set() or self._failed:
                    return None
                self._cv.wait(min(left, 0.2))
            return self._results.pop(jid)

    def _cancel_queued(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                with self._lock:
                    self._proc.stdin.write(json.dumps({"op": "cancel_all", "upto": self._next_id}) + "\n")
                    self._proc.stdin.flush()
            except OSError:
                pass

    # ------------------------------------------------------------------ phrase cache

    def _cache_path(self, text: str) -> Path:
        key = f"{text}|{self.cfg.get('temperature', 0.8)}"
        return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".wav")

    def _warm_cache(self):
        for phrase in self._phrases:
            path = self._cache_path(phrase)
            if path.exists():
                continue
            res = self._wait(self._submit(phrase), float(self.cfg.get("sentence_timeout_s", 90)))
            if res and res.get("ok"):
                shutil.move(res["path"], path)
                print(f"[TTS] Cached phrase: {phrase!r}")

    def prepare(self, text: str) -> None:
        """Render text into the phrase cache in the background, so speak() plays it instantly
        (the next window greeting). Stop/cancel drops it like any queued sentence."""
        threading.Thread(target=self._cache_text, args=(text,), name="chatterbox-prepare", daemon=True).start()

    def _cache_text(self, text: str) -> None:
        if not self._ready.wait(float(self.cfg.get("startup_timeout_s", 600))) or self._failed:
            return
        for sentence in split_sentences(text):
            path = self._cache_path(sentence)
            if path.exists():
                continue
            res = self._wait(self._submit(sentence), float(self.cfg.get("sentence_timeout_s", 90)))
            if res and res.get("ok"):
                shutil.move(res["path"], path)

    def discard(self, text: str) -> None:
        """Delete text's cached audio once it will not be spoken again (fixed phrases are kept)."""
        for sentence in split_sentences(text):
            if sentence not in self._phrases:
                self._cache_path(sentence).unlink(missing_ok=True)

    # ------------------------------------------------------------------ engine API

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and not self._failed

    @property
    def sample_rate(self) -> int:
        return self._sr

    def synthesize(self, text: str) -> np.ndarray:
        """Whole text as int16 PCM at sample_rate (waits for the model; used by tests and tools)."""
        if not self._ready.wait(float(self.cfg.get("startup_timeout_s", 600))) or self._failed:
            raise RuntimeError(self._failed or "Chatterbox is not ready")
        chunks = []
        for sentence in split_sentences(text):
            res = self._wait(self._submit(sentence), float(self.cfg.get("sentence_timeout_s", 90)))
            if not res or not res.get("ok"):
                raise RuntimeError((res or {}).get("error", "Chatterbox timed out"))
            audio, _ = read_wav(res["path"])
            Path(res["path"]).unlink(missing_ok=True)
            chunks.append(audio)
        return np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)

    def speak(self, text: str, blocking: bool = True) -> Optional[dict]:
        self._stop.clear()
        if not self.ready:
            if self.fallback is not None:
                print("[TTS] Chatterbox not ready yet — Piper speaks this one")
                return self.fallback.speak(text, blocking=True)
            return None
        t0 = time.time()
        sentences = split_sentences(text)
        jobs = []                                 # (sentence, cached_path | job id)
        for s in sentences:
            cached = self._cache_path(s)
            jobs.append((s, cached if cached.exists() else self._submit(s)))
        first_audio = None
        played, synth_s = 0.0, 0.0
        timeout = float(self.cfg.get("sentence_timeout_s", 90))
        for i, (sentence, job) in enumerate(jobs):
            if self._stop.is_set():
                break
            if isinstance(job, Path):
                audio, sr = read_wav(job)
            else:
                t = time.time()
                res = self._wait(job, timeout)
                synth_s += time.time() - t
                if self._stop.is_set():
                    break
                if not res or not res.get("ok"):
                    why = (res or {}).get("error", "timed out")
                    print(f"[TTS] Chatterbox failed on a sentence ({why})" + (" — Piper finishes the reply" if self.fallback else ""))
                    self._cancel_queued()
                    if self.fallback is not None:
                        self.fallback.speak(" ".join(s for s, _ in jobs[i:]), blocking=True)
                    break
                audio, sr = read_wav(res["path"])
                Path(res["path"]).unlink(missing_ok=True)
            if first_audio is None:
                first_audio = time.time() - t0
            sd.play(audio, samplerate=sr, blocking=True)
            played += len(audio) / sr
        if self._stop.is_set():
            self._cancel_queued()
        return {"synthesis_time": synth_s, "audio_duration": played, "playback_time": played,
                "first_audio_s": round(first_audio or 0.0, 2), "engine": "chatterbox"}

    def stop(self):
        """Stop speaking now and drop sentences that are still queued."""
        self._stop.set()
        self._cancel_queued()
        try:
            sd.stop()
        except Exception:
            pass

    def close(self):
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()               # the worker exits when stdin closes
            proc.wait(5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
