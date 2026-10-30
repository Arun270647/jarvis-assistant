"""Fish Audio voice for Jarvis (tts.engine = "fish"), from Fish Audio's cloud or the fish-speech engine on this computer.

tts.fish.mode picks where the speech is made:
  - "cloud": Fish Audio's API (https://api.fish.audio/v1/tts) speaks with a voice from fish.audio, by its id
    (tts.fish.voice_id, the hex part of https://fish.audio/m/<id>/). The voice is Fish Audio's own model, so it
    sounds exactly as on the site. Needs internet and the user's Fish Audio API key, which is kept in the OS secret
    store (JARVIS/TTS/fish; the FISH_API_KEY environment variable works too, for developers). Fish Audio bills
    the key's account per character.
  - "local": fish-speech (Fish Audio's open-source engine, installed by scripts/setup_fish.py into its own Python
    in the models folder) runs as a server on 127.0.0.1, started hidden and loaded once. It clones the voice in
    tts.fish.reference_audio (a clean 10-30 s clip; tts.fish.reference_text is what the clip says). Offline, but
    the open model is smaller than Fish Audio's cloud one, so the voice is close rather than identical. The open
    weights are for non-commercial use only (Fish Audio Research License / CC-BY-NC-SA-4.0).

Both modes take the same request (fish-speech's ServeTTSRequest). Replies are voiced sentence by sentence: the
next sentence is made while the current one plays. Fixed phrases (tts.fish.cache_phrases) and prepare()d texts
are kept in tts/fish in the cache folder, so they play at once and cost nothing again. tts.fish.fallback (Kokoro
by default) speaks while the local server loads, without a key, or when a request fails.
"""
import base64
import hashlib
import json
import os
import queue
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from src.core import paths, winproc
from src.tts import split_sentences

CLOUD_URL = "https://api.fish.audio"
CLOUD_MODELS = ("s2.1-pro", "s2.1-pro-free", "s2-pro", "s1")
DEFAULT_VOICE_ID = "29c31f6e158c4c8f84c14f9f3d8ba008"     # "Jarvis" on fish.audio
CACHE_DIR = paths.cache_dir() / "tts" / "fish"
KEY_TARGET = "JARVIS/TTS/fish"
KEY_USER = "api-key"
KEY_ENV = "FISH_API_KEY"


# ---------------------------------------------------------------------- the API key (cloud mode)

def api_key() -> tuple:
    """(key, source): source is "saved" (secret store), "env:FISH_API_KEY" or None when there is no key."""
    from src.core import credentials
    key = credentials.read(KEY_TARGET)
    if key:
        return key, "saved"
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV], f"env:{KEY_ENV}"
    return None, None


def save_api_key(key: str) -> None:
    from src.core import credentials
    key = (key or "").strip()
    if not key:
        raise ValueError("Empty API key")
    credentials.write(KEY_TARGET, KEY_USER, key)


def forget_api_key() -> None:
    from src.core import credentials
    credentials.delete(KEY_TARGET)


# ---------------------------------------------------------------------- audio

def parse_wav(data: bytes) -> tuple:
    """(int16 mono PCM, sample rate) from WAV bytes. A streamed WAV gives no real sizes in its header, so the
    data chunk runs to the end of the bytes."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Fish Audio did not answer with WAV audio")
    pos, channels, sr, bits = 12, 1, 44100, 16
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt ":
            channels, sr = struct.unpack("<HI", data[body + 2:body + 8])
            bits = struct.unpack("<H", data[body + 14:body + 16])[0]
        elif cid == b"data":
            raw = data[body:] if size in (0, 0xFFFFFFFF) or body + size > len(data) else data[body:body + size]
            if bits != 16:
                raise ValueError(f"Fish Audio sent {bits}-bit audio; 16-bit was asked for")
            audio = np.frombuffer(raw[:len(raw) // 2 * 2], dtype=np.int16)
            if channels > 1:
                audio = audio[:len(audio) // channels * channels].reshape(-1, channels).mean(axis=1).astype(np.int16)
            return audio.copy(), int(sr)
        pos = body + size + (size & 1)
    raise ValueError("Fish Audio's WAV had no audio data")


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.astype(np.int16).tobytes())


def read_wav(path: Path) -> tuple:
    return parse_wav(Path(path).read_bytes())


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FishTTS:
    def __init__(self, config: dict, fallback=None, opener=None):
        tts_cfg = config.get("tts", {}) or {}
        self.cfg = tts_cfg.get("fish", {}) or {}
        self.mode = self.cfg.get("mode", "cloud")
        self.fallback = fallback
        # fish-speech answers on 127.0.0.1, which must not go through a system proxy
        self._open = opener or (urllib.request.urlopen if self.mode == "cloud"
                                else urllib.request.build_opener(urllib.request.ProxyHandler({})).open)
        self._sr = 44100
        self._proc = None
        self._url = ""
        self._key = None
        self._reference = None                  # local mode: {"audio": base64, "text": ...}
        self._ready = threading.Event()
        self._failed: Optional[str] = None
        self._stop = threading.Event()
        self._gen = 0                            # bumped by stop(): sentences of an older reply are dropped
        from src.core.config_store import assistant_name, with_name
        name = assistant_name()
        self._phrases = [with_name(p, name).strip() for p in self.cfg.get("cache_phrases", []) if p and p.strip()]
        voice = self.cfg.get("voice_id") or DEFAULT_VOICE_ID
        self.voice_name = f"fish {voice}" if self.mode == "cloud" else "fish local"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if self.mode == "cloud":
            self._start_cloud()
        elif self.mode == "local":
            threading.Thread(target=self._start_local, name="fish-start", daemon=True).start()
        else:
            self._fail(f"Unknown Fish Audio mode {self.mode!r} (tts.fish.mode: cloud or local)")

    # ------------------------------------------------------------------ starting

    def _start_cloud(self):
        self._key = api_key()[0]
        if not self._key:
            return self._fail("No Fish Audio API key: add it in Settings › Voice (fish.audio › API keys)")
        self._url = (self.cfg.get("api_url") or CLOUD_URL).rstrip("/") + "/v1/tts"
        self._ready.set()
        threading.Thread(target=self._warm_cache, name="fish-cache", daemon=True).start()

    def _start_local(self):
        python, repo, ckpt = (self.cfg.get(k, "") for k in ("python", "repo", "checkpoint"))
        if not python or not Path(python).exists():
            return self._fail(f"fish-speech python not found: {python!r} (run scripts/setup_fish.py)")
        if not repo or not (Path(repo) / "tools" / "api_server.py").exists():
            return self._fail(f"fish-speech not found in {repo!r} (run scripts/setup_fish.py)")
        if not ckpt or not Path(ckpt).is_dir():
            return self._fail(f"fish-speech model not found: {ckpt!r} (run scripts/setup_fish.py)")
        ref = self.cfg.get("reference_audio", "")
        if not ref or not Path(ref).is_file():
            return self._fail("The local Fish voice needs a clip to clone: set tts.fish.reference_audio "
                              "(or run scripts/setup_fish.py --reference <clip>)")
        self._reference = {"audio": base64.b64encode(Path(ref).read_bytes()).decode("ascii"),
                           "text": self.cfg.get("reference_text", "")}
        port = free_port()
        device = self.cfg.get("device", "cuda")
        cmd = [python, "-m", "tools.api_server", "--llama-checkpoint-path", ckpt,
               "--decoder-checkpoint-path", str(Path(ckpt) / "codec.pth"), "--device", device,
               "--listen", f"127.0.0.1:{port}"]
        if device == "cuda" and self.cfg.get("half", True):
            cmd.append("--half")
        env = dict(os.environ)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1"})
        t0 = time.time()
        try:
            import subprocess
            self._proc = winproc.popen_hidden(cmd, cwd=repo, env=env, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
        except Exception as e:
            return self._fail(f"Could not start fish-speech: {e}")
        base = f"http://127.0.0.1:{port}"
        timeout = float(self.cfg.get("startup_timeout_s", 600))
        while time.time() - t0 < timeout:
            if self._proc is None or self._proc.poll() is not None:
                return self._fail("fish-speech exited while loading (try it in a terminal: "
                                  f"{subprocess.list2cmdline(cmd)})")
            try:
                with self._open(urllib.request.Request(base + "/v1/health"), timeout=5) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(1.0)
        else:
            self._fail(f"fish-speech did not load within {timeout:.0f} s")
            return self.close()
        self._url = base + "/v1/tts"
        self._ready.set()
        print(f"[TTS] fish-speech ready ({time.time() - t0:.1f}s, {device})")
        self._warm_cache()

    def _fail(self, why: str):
        self._failed = why
        print(f"[TTS] {why}" + (" — using the fallback voice" if self.fallback else ""))

    # ------------------------------------------------------------------ one request

    def _body(self, text: str) -> dict:
        body = {"text": text, "format": "wav", "normalize": True,
                "temperature": float(self.cfg.get("temperature", 0.7)), "top_p": float(self.cfg.get("top_p", 0.7))}
        if self.mode == "cloud":
            body.update({"reference_id": self.cfg.get("voice_id") or DEFAULT_VOICE_ID, "sample_rate": 44100,
                         "latency": self.cfg.get("latency", "balanced")})
        else:
            body.update({"references": [self._reference], "use_memory_cache": "on"})
        return body

    def _request(self, text: str) -> tuple:
        """(int16 PCM, sample rate) for one sentence. Raises RuntimeError with a plain reason."""
        headers = {"Content-Type": "application/json"}
        if self.mode == "cloud":
            headers["Authorization"] = f"Bearer {self._key}"
            if self.cfg.get("cloud_model"):
                headers["model"] = self.cfg["cloud_model"]
        req = urllib.request.Request(self._url, data=json.dumps(self._body(text)).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with self._open(req, timeout=float(self.cfg.get("sentence_timeout_s", 60))) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(self._http_error(e)) from e
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise RuntimeError(f"Fish Audio could not be reached: {getattr(e, 'reason', e)}") from e
        return parse_wav(data)

    @staticmethod
    def _http_error(e) -> str:
        try:
            detail = json.loads(e.read() or b"{}").get("message", "")
        except (ValueError, OSError, AttributeError):
            detail = ""
        meaning = {401: "the API key was refused", 402: "the Fish Audio account is out of credit",
                   404: "the voice id was not found", 429: "too many requests"}.get(e.code, f"HTTP {e.code}")
        return f"Fish Audio: {meaning}" + (f" ({detail})" if detail else "")

    # ------------------------------------------------------------------ phrase cache

    def _cache_path(self, text: str) -> Path:
        voice = self.cfg.get("voice_id") if self.mode == "cloud" else self.cfg.get("reference_audio")
        key = "|".join(str(x) for x in (self.mode, voice, self.cfg.get("cloud_model"), self.cfg.get("temperature", 0.7),
                                        text))
        return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".wav")

    def _cache(self, sentence: str) -> None:
        path = self._cache_path(sentence)
        if path.exists():
            return
        try:
            audio, sr = self._request(sentence)
        except RuntimeError as e:
            print(f"[TTS] Could not cache {sentence!r}: {e}")
            return
        tmp = path.with_name(path.name + ".part")
        write_wav(tmp, audio, sr)
        os.replace(tmp, path)

    def _warm_cache(self):
        for phrase in self._phrases:
            self._cache(phrase)

    def prepare(self, text: str) -> None:
        """Make text's audio in the background, so speak() plays it at once (the next window greeting)."""
        def run():
            if not self._wait_ready():
                return
            for sentence in split_sentences(text):
                self._cache(sentence)
        threading.Thread(target=run, name="fish-prepare", daemon=True).start()

    def discard(self, text: str) -> None:
        """Delete text's cached audio once it will not be spoken again (fixed phrases are kept)."""
        for sentence in split_sentences(text):
            if sentence not in self._phrases:
                self._cache_path(sentence).unlink(missing_ok=True)

    # ------------------------------------------------------------------ engine API

    def _wait_ready(self) -> bool:
        """Wait for the local server to load; False at once when the engine failed."""
        end = time.time() + float(self.cfg.get("startup_timeout_s", 600))
        while not self._failed and time.time() < end:
            if self._ready.wait(0.2):
                break
        return self.ready

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and not self._failed

    @property
    def sample_rate(self) -> int:
        return self._sr

    def _audio(self, sentence: str) -> tuple:
        cached = self._cache_path(sentence)
        if cached.exists():
            return read_wav(cached)
        return self._request(sentence)

    def synthesize(self, text: str) -> np.ndarray:
        """Whole text as int16 PCM at sample_rate (waits for the local server; used by tests and tools)."""
        if not self._wait_ready():
            raise RuntimeError(self._failed or "Fish Audio is not ready")
        chunks = []
        for sentence in split_sentences(text):
            audio, self._sr = self._audio(sentence)
            chunks.append(audio)
        return np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)

    def speak(self, text: str, blocking: bool = True) -> Optional[dict]:
        self._stop.clear()
        if not self.ready:
            if self.fallback is not None:
                print("[TTS] Fish Audio not ready — the fallback voice speaks this one")
                return self.fallback.speak(text, blocking=True)
            return None
        t0 = time.time()
        gen = self._gen
        sentences = split_sentences(text)
        made: "queue.Queue" = queue.Queue()        # the next sentences are made while one plays

        def produce():
            for i, sentence in enumerate(sentences):
                if self._stop.is_set() or gen != self._gen:
                    break
                t = time.time()
                try:
                    audio, sr = self._audio(sentence)
                except RuntimeError as e:
                    made.put((i, None, str(e), time.time() - t))
                    return
                made.put((i, (audio, sr), None, time.time() - t))
            made.put(None)

        threading.Thread(target=produce, name="fish-make", daemon=True).start()
        first_audio, played, synth_s = None, 0.0, 0.0
        while True:
            item = made.get()
            if item is None or self._stop.is_set():
                break
            i, result, error, took = item
            synth_s += took
            if error:
                print(f"[TTS] {error}" + (" — the fallback voice finishes the reply" if self.fallback else ""))
                if self.fallback is not None:
                    self.fallback.speak(" ".join(sentences[i:]), blocking=True)
                break
            audio, sr = result
            self._sr = sr
            if first_audio is None:
                first_audio = time.time() - t0
            sd.play(audio, samplerate=sr, blocking=True)
            played += len(audio) / sr
        return {"synthesis_time": synth_s, "audio_duration": played, "playback_time": played,
                "first_audio_s": round(first_audio or 0.0, 2), "engine": "fish"}

    def stop(self):
        """Stop speaking now; sentences not made yet are dropped."""
        self._stop.set()
        self._gen += 1
        try:
            sd.stop()
        except Exception:
            pass

    def close(self):
        self.stop()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
