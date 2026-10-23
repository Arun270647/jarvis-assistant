"""The voice built into the operating system: nothing to download, so a fresh install can always speak.

Windows: SAPI 5 through COM (pywin32), synthesized into memory and played like the other engines, so the
pipeline's microphone draining works the same. tts.system.voice picks a voice by part of its name
("Zira"); empty means the Windows default. tts.system.rate is -10 (slow) to 10 (fast). macOS (`say`)
follows with the Mac port.
"""
import threading
import time
from typing import Optional

import numpy as np

from src.tts import TTSEngine

SAMPLE_RATE = 22050
SAPI_22KHZ_16BIT_MONO = 22          # SpeechAudioFormatType.SAFT22kHz16BitMono


def _voice_for_this_thread(cache: threading.local, wanted: str, rate: int):
    """SAPI objects belong to the COM apartment of the thread that made them: one SpVoice per thread."""
    voice = getattr(cache, "voice", None)
    if voice is None:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        if wanted:
            tokens = voice.GetVoices()
            for i in range(tokens.Count):
                if wanted.lower() in tokens.Item(i).GetDescription().lower():
                    voice.Voice = tokens.Item(i)
                    break
        voice.Rate = max(-10, min(10, int(rate or 0)))
        cache.voice = voice
    return voice


def system_voices() -> list:
    """Names of the voices Windows offers (empty elsewhere or when SAPI is unavailable)."""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        tokens = win32com.client.Dispatch("SAPI.SpVoice").GetVoices()
        return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
    except Exception:
        return []


class SystemTTS(TTSEngine):
    voice_name = "system"

    def __init__(self, config: Optional[dict] = None):
        if config is None:
            from src.tts import load_config
            config = load_config().get("tts", {}).get("system", {}) or {}
        self.wanted = str(config.get("voice") or "")
        self.rate = int(config.get("rate") or 0)
        self._local = threading.local()
        t0 = time.time()
        if len(self.synthesize("test")) == 0:
            raise RuntimeError("The system voice produced no audio")
        print(f"[TTS] System voice ready ({(time.time() - t0) * 1000:.0f}ms{', ' + self.wanted if self.wanted else ''})")

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def synthesize(self, text: str) -> np.ndarray:
        import win32com.client
        voice = _voice_for_this_thread(self._local, self.wanted, self.rate)
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = SAPI_22KHZ_16BIT_MONO
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        stream.Format = fmt
        voice.AudioOutputStream = stream
        voice.Speak(text, 0)
        return np.frombuffer(bytes(stream.GetData()), dtype=np.int16).copy()

    def speak(self, text: str, blocking: bool = True) -> Optional[dict]:
        import sounddevice as sd
        try:
            t0 = time.time()
            audio = self.synthesize(text)
            synthesis_time = time.time() - t0
            t1 = time.time()
            sd.play(audio, samplerate=SAMPLE_RATE, blocking=blocking)
            return {"synthesis_time": synthesis_time, "audio_duration": len(audio) / SAMPLE_RATE,
                    "playback_time": time.time() - t1 if blocking else 0.0}
        except Exception as e:
            print(f"[TTS ERROR] {e}")
            return None
