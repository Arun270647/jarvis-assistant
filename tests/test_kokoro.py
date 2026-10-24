"""The Kokoro voice (src/tts/kokoro.py) with a stand-in for kokoro-onnx, so nothing is downloaded or played: the
voices file is read, the accent picks espeak-ng's language, a missing download or engine add-on falls back to the
system voice, replies are voiced sentence by sentence, and stop() drops what hasn't played yet."""
import shutil
import sys
import types
import unittest
import uuid
from unittest import mock

import numpy as np

import src.tts as tts_module
from src.core import addons, paths
from src.tts import kokoro


class FakeKokoro:
    instances = []

    def __init__(self, model_path, voices_path):
        self.model_path, self.voices_path, self.calls = model_path, voices_path, []
        FakeKokoro.instances.append(self)

    def get_voices(self):
        return ["af_heart", "bf_emma"]

    def create(self, text, voice, speed=1.0, lang="en-us"):
        self.calls.append((text, voice, speed, lang))
        return np.full(2400, 0.5, dtype=np.float32), 24000


class KokoroTests(unittest.TestCase):
    def setUp(self):
        self.dir_name = f"kokoro-test-{uuid.uuid4().hex[:8]}"
        self.folder = paths.model_path(self.dir_name)
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.settings = {"model_dir": self.dir_name, "model": kokoro.DEFAULT_MODEL, "voice": "af_heart", "speed": 1.0}
        FakeKokoro.instances = []

    def download(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / kokoro.DEFAULT_MODEL).write_bytes(b"onnx")
        with open(self.folder / kokoro.VOICES_FILE, "wb") as f:
            np.savez(f, af_heart=np.zeros(3), bm_george=np.zeros(3), bf_emma=np.zeros(3))

    def engine(self, **settings):
        fake = types.ModuleType("kokoro_onnx")
        fake.Kokoro = FakeKokoro
        with mock.patch.dict(sys.modules, {"kokoro_onnx": fake}), mock.patch.object(addons, "installed", return_value=True):
            return kokoro.KokoroTTS({**self.settings, **settings})

    def test_the_accent_letter_picks_the_language(self):
        self.assertEqual([kokoro.language(v) for v in ("af_heart", "bm_george", "ef_dora", "", "x_unknown")],
                         ["en-us", "en-gb", "es", "en-us", "en-us"])

    def test_voices_come_from_the_downloaded_file(self):
        cfg = {"tts": {"kokoro": self.settings}}
        self.assertEqual(kokoro.voices(cfg), [])
        self.download()
        self.assertEqual(kokoro.voices(cfg), ["af_heart", "bf_emma", "bm_george"])

    def test_not_downloaded_or_no_engine_says_how_to_fix_it(self):
        with self.assertRaises(FileNotFoundError) as e:
            self.engine()
        self.assertIn("Settings › Models", str(e.exception))
        self.download()
        with mock.patch.object(addons, "installed", return_value=False), self.assertRaises(RuntimeError) as e:
            kokoro.KokoroTTS(self.settings)
        self.assertIn("engine isn't installed", str(e.exception))

    def test_an_unknown_voice_is_refused(self):
        self.download()
        with self.assertRaises(ValueError):
            self.engine(voice="zz_nobody")

    def test_synthesizes_int16_at_24_khz_with_the_voice_speed_and_accent(self):
        self.download()
        tts = self.engine(voice="bf_emma", speed=5)
        audio = tts.synthesize("Hello.")
        self.assertEqual((audio.dtype, tts.sample_rate, int(audio[0])), (np.int16, 24000, 16383))
        self.assertEqual(FakeKokoro.instances[-1].calls[-1], ("Hello.", "bf_emma", 2.0, "en-gb"))
        self.assertEqual(tts.voice_name, "kokoro bf_emma")

    def test_speaks_sentence_by_sentence_in_order(self):
        self.download()
        tts = self.engine()
        played = []
        with mock.patch("sounddevice.play", side_effect=lambda audio, **kw: played.append(len(audio))):
            result = tts.speak("One. Two? Three!")
        spoken = [c[0] for c in FakeKokoro.instances[-1].calls[1:]]          # [0] is the start-up test
        self.assertEqual(spoken, ["One.", "Two?", "Three!"])
        self.assertEqual(played, [2400, 2400, 2400])
        self.assertEqual(result["engine"], "kokoro")
        self.assertAlmostEqual(result["audio_duration"], 0.3)

    def test_stop_drops_the_sentences_not_played_yet(self):
        self.download()
        tts = self.engine()
        played = []

        def play(audio, **kw):
            played.append(len(audio))
            tts.stop()                                          # the user interrupts during the first sentence

        with mock.patch("sounddevice.play", side_effect=play), mock.patch("sounddevice.stop"):
            result = tts.speak("One. Two. Three.")
        self.assertEqual(played, [2400])
        self.assertAlmostEqual(result["audio_duration"], 0.1)

    def test_missing_kokoro_falls_back_to_the_system_voice(self):
        cfg = {"tts": {"engine": "kokoro", "fallback": "system", "kokoro": self.settings, "system": {}}}
        system = mock.MagicMock(voice_name="system")
        with mock.patch("src.tts.system.SystemTTS", return_value=system):
            self.assertIs(tts_module.create_tts_engine(cfg), system)
        cfg["tts"]["fallback"] = "none"
        with self.assertRaises(FileNotFoundError):
            tts_module.create_tts_engine(cfg)

    def test_a_voice_can_be_heard_before_it_is_chosen(self):
        self.download()
        cfg = {"tts": {"engine": "system", "kokoro": self.settings}}
        fake = types.ModuleType("kokoro_onnx")
        fake.Kokoro = FakeKokoro
        with mock.patch.dict(sys.modules, {"kokoro_onnx": fake}), mock.patch.object(addons, "installed", return_value=True):
            tts = tts_module.preview_engine({"tts.engine": "kokoro", "tts.kokoro.voice": "bf_emma"}, cfg)
        self.assertEqual((tts.voice, tts.lang), ("bf_emma", "en-gb"))
        self.assertEqual((cfg["tts"]["engine"], cfg["tts"]["kokoro"]["voice"]), ("system", "af_heart"), "settings untouched")

    def test_chatterbox_falls_back_to_the_engine_its_settings_name(self):
        cfg = {"tts": {"engine": "chatterbox", "chatterbox": {"fallback": "system"}}}
        system = object()
        with mock.patch("src.tts.system.SystemTTS", return_value=system), \
                mock.patch("src.tts.chatterbox.ChatterboxTTS") as chatterbox:
            tts_module.build_engine("chatterbox", cfg)
        self.assertIs(chatterbox.call_args.kwargs["fallback"], system)
        cfg["tts"]["chatterbox"]["fallback"] = "none"
        with mock.patch("src.tts.chatterbox.ChatterboxTTS") as chatterbox:
            tts_module.build_engine("chatterbox", cfg)
        self.assertIsNone(chatterbox.call_args.kwargs["fallback"])


if __name__ == "__main__":
    unittest.main()
