"""The voice built into the operating system: nothing to download, so a fresh install can always speak.

Windows: SAPI 5 through COM (pywin32), synthesized into memory. macOS: the `say` command, which writes 16-bit WAV
into a temporary file. Both are played like the other engines, so the pipeline's microphone draining works the
same. tts.system.voice picks a voice by part of its name ("Zira", "Samantha"); empty means the system default.
tts.system.rate is -10 (slow) to 10 (fast).
"""
import re
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from src.tts import TTSEngine

SAMPLE_RATE = 22050
SAPI_22KHZ_16BIT_MONO = 22          # SpeechAudioFormatType.SAFT22kHz16BitMono
SAY = "/usr/bin/say"
SAY_VOICE_LINE = re.compile(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9]{2,4})\s+#")


# ---------------------------------------------------------------------- Windows (SAPI)

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


class _Sapi:
    def __init__(self, wanted: str, rate: int):
        self.wanted, self.rate = wanted, rate
        self._local = threading.local()

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


def _sapi_voices() -> list:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    tokens = win32com.client.Dispatch("SAPI.SpVoice").GetVoices()
    return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]


# ---------------------------------------------------------------------- macOS (say)

def parse_say_voices(listing: str) -> list:
    """Voice names from `say -v ?` ("Samantha            en_US    # Hello! My name is Samantha."), English first."""
    voices = []
    for line in listing.splitlines():
        m = SAY_VOICE_LINE.match(line)
        if m:
            voices.append((not m.group(2).lower().startswith("en"), m.group(1).strip()))
    return [name for _, name in sorted(voices)]


class _Say:
    def __init__(self, wanted: str, rate: int):
        self.voice = next((v for v in _say_voices() if wanted.lower() in v.lower()), "") if wanted else ""
        # Words per minute: macOS speaks about 175 by default.
        self.words_per_minute = 175 + 12 * max(-10, min(10, int(rate or 0)))

    def synthesize(self, text: str) -> np.ndarray:
        from src.core import winproc
        with tempfile.TemporaryDirectory(prefix="jarvis-say-") as folder:
            out = Path(folder) / "speech.wav"
            command = [SAY, "-o", str(out), "--file-format=WAVE", f"--data-format=LEI16@{SAMPLE_RATE}",
                       "-r", str(self.words_per_minute)] + (["-v", self.voice] if self.voice else [])
            # The text goes in on stdin, so text starting with "-" is never read as an option.
            winproc.run_hidden(command, input=text, text=True, encoding="utf-8", timeout=60, check=True,
                               capture_output=True)
            with wave.open(str(out), "rb") as w:
                return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def _say_voices() -> list:
    from src.core import winproc
    res = winproc.run_hidden([SAY, "-v", "?"], timeout=15, capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
    return parse_say_voices(res.stdout)


# ---------------------------------------------------------------------- the engine

def system_voices() -> list:
    """Names of the voices the OS offers (empty when there is no system voice)."""
    try:
        if sys.platform == "darwin":
            return _say_voices()
        if sys.platform == "win32":
            return _sapi_voices()
    except Exception:
        pass
    return []


class SystemTTS(TTSEngine):
    voice_name = "system"

    def __init__(self, config: Optional[dict] = None):
        if config is None:
            from src.tts import load_config
            config = load_config().get("tts", {}).get("system", {}) or {}
        self.wanted = str(config.get("voice") or "")
        self.rate = int(config.get("rate") or 0)
        self.backend = _Say(self.wanted, self.rate) if sys.platform == "darwin" else _Sapi(self.wanted, self.rate)
        t0 = time.time()
        if len(self.synthesize("test")) == 0:
            raise RuntimeError("The system voice produced no audio")
        print(f"[TTS] System voice ready ({(time.time() - t0) * 1000:.0f}ms{', ' + self.wanted if self.wanted else ''})")

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def synthesize(self, text: str) -> np.ndarray:
        return self.backend.synthesize(text)

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
