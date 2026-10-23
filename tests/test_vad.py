"""Speech detection through ONNX Runtime (src/stt/vad.py), without torch: silence stays low, real speech
(synthesized by the Windows voice) goes high, and the state carries over between windows."""
import sys
import unittest

import numpy as np

from src.stt import vad


@unittest.skipIf(vad.model_file() is None, "no Silero ONNX model available (download it, or install silero-vad)")
class SileroOnnxTests(unittest.TestCase):
    def windows(self, audio):
        return [audio[i:i + vad.WINDOW] for i in range(0, len(audio) - vad.WINDOW + 1, vad.WINDOW)]

    def test_silence_is_not_speech(self):
        detector = vad.SileroVAD()
        probs = [detector(w) for w in self.windows(np.zeros(16000, dtype=np.float32))]
        self.assertLess(max(probs), 0.2)

    @unittest.skipUnless(sys.platform == "win32", "uses the Windows voice to make speech")
    def test_speech_is_detected(self):
        from src.tts.system import SAMPLE_RATE, SystemTTS
        speech = SystemTTS({}).synthesize("Hello there, this is a test of speech detection.").astype(np.float32) / 32768
        idx = np.arange(0, len(speech), SAMPLE_RATE / vad.SAMPLE_RATE).astype(int)        # 22.05 kHz -> 16 kHz
        speech16 = speech[idx[idx < len(speech)]]
        detector = vad.SileroVAD()
        probs = [detector(w) for w in self.windows(speech16)]
        self.assertGreater(max(probs), 0.8)
        self.assertGreater(sum(p > 0.5 for p in probs) / len(probs), 0.3)

    def test_rejects_wrong_window_sizes(self):
        with self.assertRaises(ValueError):
            vad.SileroVAD()(np.zeros(256, dtype=np.float32))

    def test_no_torch_needed(self):
        vad.SileroVAD()(np.zeros(vad.WINDOW, dtype=np.float32))
        self.assertNotIn("silero_vad", sys.modules, "the package (which imports torch) is never imported")


if __name__ == "__main__":
    unittest.main()
