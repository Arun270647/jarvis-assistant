"""Benchmark STT models: the configured model against small and medium (on the configured device).

Records your voice saying a test phrase, then transcribes it with each model
and reports accuracy and timing. Model names are downloaded into models/whisper
on first use. Run it before changing the default model size or compute type.

Usage:
    python scripts/benchmark_stt.py                    # record and benchmark
    python scripts/benchmark_stt.py <wav_file>         # benchmark existing recording
    python scripts/benchmark_stt.py --record-only      # just record, save to file
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import paths  # noqa: E402
from src.core.config_store import load_config  # noqa: E402


def record_clip(duration_seconds: float = 5.0) -> tuple:
    """Record a clip from the microphone. Returns (audio_int16, sample_rate)."""
    import sounddevice as sd
    cfg = load_config()
    device_idx = cfg["audio"].get("input_device_index")      # None: system default
    sr = cfg["audio"]["sample_rate"]

    print(f"\nRecording {duration_seconds}s from device [{device_idx}] at {sr}Hz...")
    print("Say: 'ask Claude to list the Python files in jarvis'")
    print("Recording in 2 seconds...")
    time.sleep(2)
    print(">>> SPEAK NOW <<<")

    audio = sd.rec(
        frames=int(sr * duration_seconds),
        samplerate=sr,
        channels=1,
        dtype="int16",
        device=device_idx,
    )
    sd.wait()

    audio_flat = audio.flatten()
    rms = np.sqrt(np.mean(audio_flat.astype(np.float32) ** 2))
    print(f"Recording done. RMS: {rms:.0f}, Samples: {len(audio_flat)}")

    return audio_flat, sr


def save_wav(audio: np.ndarray, sr: int, path: Path):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio.tobytes())
    print(f"Saved: {path}")


def load_wav(path: str) -> tuple:
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return audio, sr


def benchmark_model(model_path: str, audio: np.ndarray, sr: int,
                    initial_prompt: str, label: str, device: str = "cpu", compute_type: str = "int8",
                    download_root: str = None) -> dict:
    """Benchmark a single model."""
    from faster_whisper import WhisperModel

    print(f"\n--- {label} ---")
    print(f"  Model: {model_path}")

    # Load
    print("  Loading...")
    t_load = time.time()
    model = WhisperModel(model_path, device=device, compute_type=compute_type, download_root=download_root)
    load_ms = (time.time() - t_load) * 1000
    print(f"  Loaded ({load_ms:.0f}ms)")

    # Warmup
    print("  Warming up...")
    dummy = np.zeros(16000, dtype=np.float32)
    segs, _ = model.transcribe(dummy, beam_size=1, language="en")
    list(segs)

    # Transcribe
    audio_float = audio.astype(np.float32) / 32768.0
    audio_duration = len(audio) / sr

    # Without initial_prompt
    print("  Transcribing without initial_prompt...")
    t0 = time.time()
    segs, info = model.transcribe(audio_float, beam_size=5, language="en")
    text_no_prompt = " ".join(s.text.strip() for s in segs)
    time_no_prompt = (time.time() - t0) * 1000

    # With initial_prompt
    print("  Transcribing with initial_prompt...")
    t0 = time.time()
    segs, info = model.transcribe(audio_float, beam_size=5, language="en",
                                  initial_prompt=initial_prompt)
    text_with_prompt = " ".join(s.text.strip() for s in segs)
    time_with_prompt = (time.time() - t0) * 1000

    rtf_no = (time_no_prompt / 1000.0) / audio_duration
    rtf_with = (time_with_prompt / 1000.0) / audio_duration

    print("\n  Results:")
    print(f"    Without prompt: \"{text_no_prompt}\"")
    print(f"      Time: {time_no_prompt:.0f}ms (RTF: {rtf_no:.2f}x)")
    print(f"    With prompt:    \"{text_with_prompt}\"")
    print(f"      Time: {time_with_prompt:.0f}ms (RTF: {rtf_with:.2f}x)")

    # Cleanup
    del model
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    return {
        "label": label,
        "model_path": model_path,
        "text_no_prompt": text_no_prompt,
        "text_with_prompt": text_with_prompt,
        "time_no_prompt_ms": time_no_prompt,
        "time_with_prompt_ms": time_with_prompt,
        "rtf_no_prompt": rtf_no,
        "rtf_with_prompt": rtf_with,
        "audio_duration": audio_duration,
    }


def main():
    from src.stt.transcriber import _build_initial_prompt, _load_profile

    record_only = "--record-only" in sys.argv
    wav_file = None
    for arg in sys.argv[1:]:
        if arg.endswith(".wav"):
            wav_file = arg

    profile = _load_profile()
    initial_prompt = _build_initial_prompt(profile)

    print("=" * 70)
    print("  STT Model Benchmark")
    print("=" * 70)
    print(f"  Initial prompt: {initial_prompt}")

    # Get audio
    if wav_file:
        print(f"\n  Loading: {wav_file}")
        audio, sr = load_wav(wav_file)
    else:
        audio, sr = record_clip(5.0)
        # Save for reuse (the STT warmup also plays it)
        from src.stt.transcriber import WARMUP_CLIP
        out_path = paths.cache_dir() / WARMUP_CLIP
        out_path.parent.mkdir(parents=True, exist_ok=True)
        save_wav(audio, sr, out_path)

    if record_only:
        return 0

    audio_duration = len(audio) / sr
    print(f"\n  Audio: {audio_duration:.2f}s, {len(audio)} samples")

    # Models to test
    stt = load_config()["stt"]
    names = [stt["model_path"]] + [m for m in ("small", "medium") if m != stt["model_path"]]

    results = []
    for name in names:
        model_path, download_root = paths.whisper_model(name)
        label = f"{name} {stt['compute_type']} on {stt['device']}"
        try:
            r = benchmark_model(model_path, audio, sr, initial_prompt, label, stt["device"], stt["compute_type"],
                                download_root)
        except Exception as e:
            print(f"\n[SKIP] {label}: {e}")
            continue
        results.append(r)

    # Summary
    if results:
        print("\n" + "=" * 70)
        print("  Comparison Summary")
        print("=" * 70)
        print(f"  {'Model':<30} {'No Prompt':<35} {'With Prompt':<35}")
        print("  " + "-" * 95)
        for r in results:
            print(f"  {r['label']:<30}")
            print(f"    Text:   {r['text_no_prompt'][:50]:<50}  {r['text_with_prompt'][:50]}")
            print(f"    Time:   {r['time_no_prompt_ms']:>6.0f}ms (RTF {r['rtf_no_prompt']:.2f}x)"
                  f"                     {r['time_with_prompt_ms']:>6.0f}ms (RTF {r['rtf_with_prompt']:.2f}x)")

        print("=" * 70)

        # Check accuracy
        target = "ask claude to list the python files in jarvis"
        print(f"\n  Target phrase: \"{target}\"")
        for r in results:
            text = r["text_with_prompt"].lower().strip()
            has_claude = "claude" in text
            has_jarvis = "jarvis" in text
            has_delegate = "ask" in text and ("claude" in text or "cloud" in text)
            print(f"  {r['label']}: 'Claude'={'✓' if has_claude else '✗'}  'jarvis'={'✓' if has_jarvis else '✗'}  delegate={'✓' if has_delegate else '✗'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
