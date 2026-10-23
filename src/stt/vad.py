"""Speech detection with Silero VAD through ONNX Runtime: no torch, so the core install stays small.

The model is the catalogue's "silero-vad" file (models/vad/silero_vad.onnx, downloaded by the setup wizard);
a copy shipped inside the optional silero-vad package is used when that is installed but the download
hasn't happened. Calls mirror Silero's own ONNX wrapper: 512-sample windows at 16 kHz, each fed with the
last 64 samples of the previous one, and the recurrent state carried from call to call.
"""
import importlib.util
from pathlib import Path
from typing import Optional

import numpy as np

from src.core import paths

SAMPLE_RATE = 16000
WINDOW = 512
CONTEXT = 64


def model_file() -> Optional[Path]:
    downloaded = paths.models_dir() / "vad" / "silero_vad.onnx"
    if downloaded.exists():
        return downloaded
    spec = importlib.util.find_spec("silero_vad")             # found without importing it (it imports torch)
    for folder in (spec.submodule_search_locations or []) if spec else []:
        shipped = Path(folder) / "data" / "silero_vad.onnx"
        if shipped.exists():
            return shipped
    return None


class SileroVAD:
    def __init__(self, path: Optional[Path] = None):
        import onnxruntime
        path = path or model_file()
        if path is None:
            raise FileNotFoundError("The speech detection model isn't downloaded yet (Settings › Models, or run setup)")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts)
        self.path = Path(path)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def __call__(self, window: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
        """Speech probability (0..1) for one window of 512 float32 samples in -1..1."""
        if sample_rate != SAMPLE_RATE or len(window) != WINDOW:
            raise ValueError(f"Silero VAD takes {WINDOW} samples at {SAMPLE_RATE} Hz")
        x = np.concatenate([self._context, np.asarray(window, dtype=np.float32).reshape(1, WINDOW)], axis=1)
        out, self._state = self.session.run(None, {"input": x, "state": self._state,
                                                   "sr": np.array(SAMPLE_RATE, dtype=np.int64)})
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])
