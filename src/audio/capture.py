"""Microphone capture. audio.input_device_index picks the device; null means the system default.

    python -m src.audio.capture list | select | record
"""
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd

from src.core import config_store, paths


def load_config():
    return config_store.load_config()


def list_devices():
    """Print all audio devices with their indices."""
    devices = sd.query_devices()
    print("\n=== Audio Input Devices ===\n")
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            mark = " *" if dev["name"] == sd.query_devices(kind="input")["name"] else ""
            print(f"  [{i}] {dev['name']} (inputs: {dev['max_input_channels']}, "
                  f"rate: {dev['default_samplerate']:.0f}){mark}")
    print("\n  * = current system default\n")
    return devices


def select_device():
    """Interactive device selection — stores choice in config."""
    devices = list_devices()
    cfg = load_config()

    current = cfg["audio"].get("input_device_index")
    print(f"Currently configured device index: {current if current is not None else 'system default'}")

    idx = input("Enter device index to use (or press Enter to keep current): ").strip()
    if idx:
        idx = int(idx)
        dev = devices[idx]
        if dev["max_input_channels"] < 1:
            print(f"ERROR: Device [{idx}] has no input channels.")
            sys.exit(1)
        config_store.update_config({"audio.input_device_index": idx})
        print(f"Saved device [{idx}] {dev['name']} to {paths.config_file()}.")


class AudioCapture:
    """Streaming audio capture for real-time processing."""

    def __init__(self):
        cfg = load_config()
        self.device_idx = cfg["audio"].get("input_device_index")      # None: the system default microphone
        self.sample_rate = cfg["audio"]["sample_rate"]
        self.chunk_size = 512  # ~32ms at 16kHz

    def stream(self):
        """Return a context manager for streaming audio input."""
        return sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            device=self.device_idx,
            blocksize=self.chunk_size,
        )


def record_test_clip(output_path: Path | None = None):
    """Record a short test clip at 16kHz mono, return the numpy array."""
    cfg = load_config()
    device_idx = cfg["audio"].get("input_device_index")
    sr = cfg["audio"]["sample_rate"]
    duration = cfg["audio"]["test_recording_seconds"]

    print(f"Recording {duration}s from device [{device_idx if device_idx is not None else 'default'}] at {sr}Hz mono...")
    print("Speak now!\n")

    audio = sd.rec(
        frames=int(sr * duration),
        samplerate=sr,
        channels=1,
        dtype="int16",
        device=device_idx,
    )
    sd.wait()

    audio_flat = audio.flatten()
    rms = np.sqrt(np.mean(audio_flat.astype(np.float32) ** 2))
    peak = np.max(np.abs(audio_flat))
    print(f"Recording complete. RMS: {rms:.1f}, Peak: {peak}, Samples: {len(audio_flat)}")

    if peak < 100:
        print("WARNING: Recording appears silent (peak < 100). Check your microphone.")

    if output_path:
        import wave
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(audio.tobytes())
        print(f"Saved to {output_path}")

    return audio_flat, sr


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Audio capture setup for Jarvis")
    parser.add_argument("command", choices=["list", "select", "record"],
                        help="list=show devices, select=pick device, record=test clip")
    parser.add_argument("--output", type=str, default=str(paths.cache_dir() / "test_recording.wav"),
                        help="Output path for test recording")
    args = parser.parse_args()

    if args.command == "list":
        list_devices()
    elif args.command == "select":
        select_device()
    elif args.command == "record":
        record_test_clip(Path(args.output))
