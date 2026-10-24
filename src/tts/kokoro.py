"""Kokoro: the default voice (tts.engine = "kokoro").

An 82-million-parameter model (Apache-2.0) that runs on the CPU through ONNX Runtime, using kokoro-onnx (MIT).
espeak-ng (GPL-3.0, through phonemizer, also GPL-3.0) turns the text into phonemes first. Those two can't ship
inside Jarvis (Apache-2.0), so the whole engine is an add-on (config/models.json "addons", src/core/addons.py):
the model downloader installs it from PyPI into models/addons/kokoro together with the first Kokoro voice.

- The model and the voices file live in models/kokoro (tts.kokoro.model_dir). The setup wizard and
  Settings › Models download them; until then tts.fallback (the system voice) speaks.
- tts.kokoro.voice is one of the voices in the voices file. Its first letter is the accent (a: American
  English, b: British English, ...), which picks espeak-ng's language. tts.kokoro.speed is 0.5–2.0.
- Replies are voiced a sentence at a time: the next sentence is synthesized while the current one plays,
  so speech starts as soon as the first sentence is ready.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

from src.core import paths
from src.tts import TTSEngine, split_sentences

VOICES_FILE = "voices-v1.0.bin"
DEFAULT_MODEL = "kokoro-v1.0.onnx"           # full precision: on the CPU it is ~5x faster than the int8 file
# The first letter of a voice name -> espeak-ng's language for it.
ACCENTS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br",
           "z": "cmn"}


def settings(cfg: Optional[dict] = None) -> dict:
    if cfg is None:
        from src.tts import load_config
        cfg = load_config()
    return (cfg.get("tts", {}) or {}).get("kokoro", {}) or {}


def model_dir(cfg: Optional[dict] = None) -> Path:
    return paths.model_path(settings(cfg).get("model_dir") or "kokoro")


def voices(cfg: Optional[dict] = None) -> list:
    """The voice names in the downloaded voices file (empty before it is downloaded)."""
    path = model_dir(cfg) / VOICES_FILE
    if not path.exists():
        return []
    try:
        with np.load(path) as data:
            return sorted(data.files)
    except Exception:
        return []


def language(voice: str) -> str:
    return ACCENTS.get((voice or "a")[:1].lower(), "en-us")


class KokoroTTS(TTSEngine):
    def __init__(self, config: Optional[dict] = None):
        config = settings() if config is None else config
        folder = paths.model_path(config.get("model_dir") or "kokoro")
        self.model_path = folder / (config.get("model") or DEFAULT_MODEL)
        self.voices_path = folder / VOICES_FILE
        self.voice = str(config.get("voice") or "af_heart")
        self.voice_name = f"kokoro {self.voice}"
        self.lang = language(self.voice)
        self.speed = min(2.0, max(0.5, float(config.get("speed") or 1.0)))
        for path in (self.model_path, self.voices_path):
            if not path.exists():
                raise FileNotFoundError(f"The Kokoro voice isn't downloaded yet ({path.name}). Download it in "
                                        "Settings › Models.")
        t0 = time.time()
        from src.core import addons
        if not addons.installed("kokoro"):
            raise RuntimeError("The Kokoro voice engine isn't installed. Click Download on a Kokoro voice in "
                               "Settings › Models to install it.")
        from kokoro_onnx import Kokoro
        self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
        if self.voice not in self._kokoro.get_voices():
            raise ValueError(f"Kokoro has no voice called {self.voice!r}. Pick one in Settings › Voice.")
        self._stop = threading.Event()
        if len(self.synthesize("test")) == 0:
            raise RuntimeError("Kokoro produced no audio")
        print(f"[TTS] Kokoro ready: {self.voice} ({self.lang}, {self.model_path.name}, "
              f"{(time.time() - t0) * 1000:.0f}ms)")

    @property
    def sample_rate(self) -> int:
        return 24000

    def synthesize(self, text: str) -> np.ndarray:
        """int16 PCM at 24 kHz."""
        audio, _ = self._kokoro.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
        return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

    def speak(self, text: str, blocking: bool = True) -> Optional[dict]:
        """Always plays to the end (or until stop()), like the other engines in the pipeline."""
        import sounddevice as sd
        self._stop.clear()
        t0 = time.time()
        first_audio, played, synth_s = None, 0.0, 0.0

        def timed(sentence):
            t = time.time()
            return self.synthesize(sentence), time.time() - t

        sentences = split_sentences(text)
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kokoro")
        try:
            pending = pool.submit(timed, sentences[0]) if sentences else None
            for i in range(len(sentences)):
                audio, took = pending.result()
                synth_s += took
                if self._stop.is_set():
                    break
                pending = pool.submit(timed, sentences[i + 1]) if i + 1 < len(sentences) else None
                if first_audio is None:
                    first_audio = time.time() - t0
                sd.play(audio, samplerate=self.sample_rate, blocking=True)
                played += len(audio) / self.sample_rate
                if self._stop.is_set():
                    break
            return {"synthesis_time": synth_s, "audio_duration": played, "playback_time": played,
                    "first_audio_s": round(first_audio or 0.0, 2), "engine": "kokoro"}
        except Exception as e:
            print(f"[TTS ERROR] {e}")
            return None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)     # a stopped reply doesn't wait for its next sentence

    def stop(self):
        """Stop speaking now; sentences not played yet are dropped."""
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
