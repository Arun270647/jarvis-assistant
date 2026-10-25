"""The OS's own voice (Windows: SAPI, macOS: say): synthesizes into memory (nothing is played), and stands in when the
chosen voice can't start, so a fresh install can always speak."""
import sys
import unittest
from unittest import mock

import numpy as np

SAY_LISTING = """Albert              en_US    # Hello! My name is Albert.
Amélie              fr_CA    # Bonjour, je m’appelle Amélie.
Bad News            en_US    # Hello! My name is Bad News.
Eddy (English (UK)) en_GB    # Hello! My name is Eddy.
Samantha            en_US    # Hello! My name is Samantha.
"""


class SayVoiceListTests(unittest.TestCase):
    def test_names_with_spaces_and_english_first(self):
        from src.tts.system import parse_say_voices
        self.assertEqual(parse_say_voices(SAY_LISTING),
                         ["Albert", "Bad News", "Eddy (English (UK))", "Samantha", "Amélie"])


@unittest.skipUnless(sys.platform in ("win32", "darwin"), "a system voice")
class SystemVoiceTests(unittest.TestCase):
    def test_synthesizes_speech(self):
        from src.tts.system import SAMPLE_RATE, SystemTTS, system_voices
        tts = SystemTTS({"voice": "", "rate": 2})
        audio = tts.synthesize("Hi, I'm Nova.")
        self.assertEqual(audio.dtype, np.int16)
        self.assertGreater(len(audio) / SAMPLE_RATE, 0.4)
        self.assertGreater(int(np.abs(audio).max()), 1000, "not silence")
        self.assertIsInstance(system_voices(), list)

    def test_text_starting_with_a_dash_is_spoken(self):
        from src.tts.system import SystemTTS
        self.assertGreater(len(SystemTTS({}).synthesize("-v is not an option here")), 0)

    def test_a_voice_by_part_of_its_name(self):
        from src.tts.system import SystemTTS, system_voices
        voices = system_voices()
        if not voices:
            self.skipTest("no system voices installed")
        if sys.platform == "darwin":
            tts = SystemTTS({"voice": voices[0].lower()})
            self.assertEqual(tts.backend.voice, voices[0])
            return
        wanted = voices[-1].split()[1]                      # e.g. "Zira" from "Microsoft Zira Desktop ..."
        tts = SystemTTS({"voice": wanted})
        self.assertIn(wanted.lower(), tts.backend._local.voice.Voice.GetDescription().lower())

    def test_missing_piper_voice_falls_back_to_the_system_voice(self):
        import src.tts as tts_module
        cfg = {"tts": {"engine": "piper", "fallback": "system", "piper": {"voice": "not-downloaded", "model_dir": "piper"},
                       "system": {}}}
        with mock.patch.object(tts_module, "load_config", return_value=cfg):
            engine = tts_module.create_tts_engine()
        self.assertEqual(engine.voice_name, "system")
        cfg["tts"]["fallback"] = "none"
        with mock.patch.object(tts_module, "load_config", return_value=cfg), self.assertRaises(FileNotFoundError):
            tts_module.create_tts_engine()


if __name__ == "__main__":
    unittest.main()
