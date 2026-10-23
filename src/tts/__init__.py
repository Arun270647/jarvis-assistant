"""Text-to-speech module for Jarvis voice responses.

tts.engine picks the voice:
  - "chatterbox": Chatterbox-Turbo in its own Python (src/tts/chatterbox.py), with Piper as the
    fallback while it loads or if it fails.
  - "piper": Piper, in-process: local, CPU-only, no VRAM (tts.piper).

After playback the pipeline drains the microphone buffer, so Jarvis's own voice
never becomes part of the next command.
"""
import time
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np
import sounddevice as sd

from src.core import paths


def load_config():
    from src.core.config_store import load_config as load_full
    return load_full()


class TTSEngine(ABC):
    """Abstract base class for TTS engines."""

    @abstractmethod
    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text to audio.

        Returns int16 PCM audio at the engine's native sample rate.
        """
        pass

    @abstractmethod
    def speak(self, text: str, blocking: bool = True) -> Optional[dict]:
        """Synthesize and play audio through default output device."""
        pass

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        pass


class PiperTTS(TTSEngine):
    """Piper TTS engine — local, CPU-only, no VRAM.

    Uses piper's in-process Python API. The ONNX voice model is loaded once
    at init and reused for every synthesis call. No subprocess spawning.
    """

    def __init__(self, config: Optional[dict] = None):
        if config is None:
            cfg = load_config()
            config = cfg.get("tts", {}).get("piper", {})

        voice = config.get("voice", "en_US-ryan-high")
        model_dir = paths.model_path(config.get("model_dir") or "piper")     # models/piper by default
        self.model_path = model_dir / f"{voice}.onnx"
        self.config_path = model_dir / f"{voice}.onnx.json"
        self._voice = None
        self._sample_rate = 22050
        self._syn_config = None

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Piper model not found: {self.model_path}\n"
                f"Voice: {voice}\n"
                "Download with: python scripts/setup_piper.py"
            )

        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Piper config not found: {self.config_path}\n"
                "Both .onnx and .onnx.json are required.\n"
                "Download with: python scripts/setup_piper.py"
            )

        # Load model once at init
        t0 = time.time()
        from piper import PiperVoice
        from piper.config import SynthesisConfig
        self._voice = PiperVoice.load(str(self.model_path), str(self.config_path))
        self._sample_rate = self._voice.config.sample_rate
        load_ms = (time.time() - t0) * 1000

        # Only set speaker_id for multi-speaker models
        speaker_id = config.get("speaker_id", 0)
        if self._voice.config.num_speakers > 1:
            self._syn_config = SynthesisConfig(speaker_id=speaker_id)

        print(f"[TTS] Piper voice loaded: {voice} ({load_ms:.0f}ms, "
              f"{self._sample_rate}Hz, speakers={self._voice.config.num_speakers})")

        # Smoke test: synthesize one phrase to catch API mismatches immediately
        test_audio = self.synthesize("test")
        assert len(test_audio) > 0, "Smoke test failed: synthesize returned empty audio"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text to int16 PCM audio. Model already in memory."""
        chunks = list(self._voice.synthesize(text, syn_config=self._syn_config))
        if not chunks:
            return np.array([], dtype=np.int16)
        return np.concatenate([c.audio_int16_array for c in chunks])

    def speak(self, text: str, blocking: bool = True) -> Optional[dict]:
        """Synthesize and play audio."""
        try:
            t_synth_start = time.time()
            audio = self.synthesize(text)
            synthesis_time = time.time() - t_synth_start
            audio_duration = len(audio) / self._sample_rate

            t_play_start = time.time()
            sd.play(audio, samplerate=self._sample_rate, blocking=blocking)
            playback_time = time.time() - t_play_start if blocking else 0.0

            return {
                "synthesis_time": synthesis_time,
                "audio_duration": audio_duration,
                "playback_time": playback_time,
            }
        except Exception as e:
            print(f"[TTS ERROR] {e}")
            return None


def create_tts_engine():
    """The engine in the settings (tts.engine)."""
    cfg = load_config()
    tts_cfg = cfg.get("tts", {})
    if tts_cfg.get("engine", "piper") == "chatterbox":
        from src.tts.chatterbox import ChatterboxTTS
        fallback = None
        if (tts_cfg.get("chatterbox", {}) or {}).get("fallback", "piper") == "piper":
            try:
                fallback = PiperTTS()
            except Exception as e:
                print(f"[TTS] Piper fallback unavailable: {e}")
        return ChatterboxTTS(cfg, fallback=fallback)
    return PiperTTS()


def _resample_int16(audio: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Simple linear resampling for int16 audio."""
    if from_sr == to_sr:
        return audio

    ratio = to_sr / from_sr
    new_length = int(len(audio) * ratio)
    indices = np.round(np.arange(0, len(audio), 1 / ratio)).astype(int)
    indices = indices[indices < len(audio)]
    return audio[indices[:new_length]]
