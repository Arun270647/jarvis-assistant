"""WAV-backed microphone for end-to-end tests of the real voice pipeline.

Behaves like sounddevice.RawInputStream for the parts the pipeline uses:
read(n) blocks in real time (audio "arrives" at the sample rate, whether or
not anyone is reading, exactly like a live mic while TTS is playing),
read_available reports what has arrived but not been read, and after the
script ends it keeps producing silence.

    source = FileAudioSource([(None, 0.5), ("data/test_open_notepad_16k.wav", 0.0), (None, 3.0)])
    pipeline.main(..., components={"audio": source})
"""
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


def load_wav_16k(path) -> np.ndarray:
    """Mono int16 at 16 kHz (linear resample if needed)."""
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit mono WAV")
        sr = wf.getframerate()
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio.astype(np.float32)).astype(np.int16)
    return audio


class _FileStream:
    def __init__(self, samples: np.ndarray, sample_rate: int, realtime: bool):
        self._samples = samples
        self._sr = sample_rate
        self._realtime = realtime
        self._pos = 0
        self._t0 = None
        self._lock = threading.Lock()

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        return False

    def _arrived(self) -> int:
        if not self._realtime:
            return 1 << 62
        return int((time.monotonic() - self._t0) * self._sr)

    @property
    def read_available(self) -> int:
        with self._lock:
            return max(0, self._arrived() - self._pos)

    def read(self, frames: int):
        with self._lock:
            need = self._pos + frames
            while self._arrived() < need:
                time.sleep(max(0.001, (need - self._arrived()) / self._sr))
            start, self._pos = self._pos, need
        chunk = self._samples[start:need] if start < len(self._samples) else np.zeros(0, dtype=np.int16)
        if len(chunk) < frames:
            chunk = np.concatenate([chunk, np.zeros(frames - len(chunk), dtype=np.int16)])
        return chunk.tobytes(), False

    @property
    def finished(self) -> bool:
        return self._pos >= len(self._samples)


class FileAudioSource:
    """AudioCapture replacement that plays a script of WAV files and silences."""

    sample_rate = 16000
    chunk_size = 512

    def __init__(self, script: List[Tuple[Optional[str], float]], realtime: bool = True):
        parts = []
        for path, pad_s in script:
            if path is not None:
                parts.append(load_wav_16k(Path(path)))
            if pad_s:
                parts.append(np.zeros(int(pad_s * self.sample_rate), dtype=np.int16))
        self.samples = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
        self.realtime = realtime
        self.last_stream: Optional[_FileStream] = None

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate

    def stream(self) -> _FileStream:
        self.last_stream = _FileStream(self.samples, self.sample_rate, self.realtime)
        return self.last_stream
