"""Benchmark TTS voices: synthesis time comparison.

Compares synthesis time for en_US-ryan-high vs en_US-amy-medium.

Usage:
    python scripts/benchmark_tts.py                  # benchmark both voices
    python scripts/benchmark_tts.py --play           # also play audio
    python scripts/benchmark_tts.py --voice ryan     # benchmark specific voice
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config_store import load_config  # noqa: E402


def benchmark_voice(voice_name: str, play: bool = False) -> dict:
    """Benchmark a single voice, loading the model in-process."""
    from src.tts import PiperTTS

    test_phrases = [
        "Opening notepad",
        "Opening calculator",
        "Volume up",
        "Done",
        "Run Claude in jarvis?",
        "Run Claude in my website project?",
        "Cancelled",
        "Delegating to Claude",
        "Opening search",
    ]

    print(f"\n{'='*60}")
    print(f"  Benchmarking: {voice_name}")
    print(f"{'='*60}")

    synthesis_times = []
    audio_durations = []

    try:
        print("[INIT] Loading voice model in-process...")
        cfg = load_config()
        piper_cfg = dict(cfg.get("tts", {}).get("piper", {}))
        piper_cfg["voice"] = voice_name
        t_load_start = time.time()
        tts = PiperTTS(config=piper_cfg)
        t_load = time.time() - t_load_start
        print(f"[INIT] Loaded ({t_load * 1000:.0f}ms)\n")

        for i, phrase in enumerate(test_phrases, 1):
            print(f"  [{i}/{len(test_phrases)}] \"{phrase}\"")

            # Synthesize
            t_synth_start = time.time()
            audio = tts.synthesize(phrase)
            t_synth = time.time() - t_synth_start
            audio_duration = len(audio) / 16000.0

            synthesis_times.append(t_synth * 1000)
            audio_durations.append(audio_duration)

            print(f"       Synthesis: {t_synth * 1000:.0f}ms | "
                  f"Audio: {audio_duration:.2f}s")

            # Play if requested
            if play:
                import sounddevice as sd
                sd.play(audio, samplerate=tts.sample_rate, blocking=True)
                print("       Played")

        # Stats
        avg_synth = sum(synthesis_times) / len(synthesis_times)
        min_synth = min(synthesis_times)
        max_synth = max(synthesis_times)
        avg_duration = sum(audio_durations) / len(audio_durations)

        print("\n  Results:")
        print(f"    Avg synthesis: {avg_synth:.0f}ms")
        print(f"    Min synthesis: {min_synth:.0f}ms")
        print(f"    Max synthesis: {max_synth:.0f}ms")
        print(f"    Avg audio duration: {avg_duration:.2f}s")

        return {
            "voice": voice_name,
            "avg_synth_ms": avg_synth,
            "min_synth_ms": min_synth,
            "max_synth_ms": max_synth,
            "avg_audio_duration": avg_duration,
            "synthesis_times": synthesis_times,
        }

    except Exception as e:
        print(f"\n[ERROR] Benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    play = "--play" in sys.argv
    specific_voice = None

    for i, arg in enumerate(sys.argv):
        if arg == "--voice" and i + 1 < len(sys.argv):
            specific_voice = sys.argv[i + 1]

    # Map short names to full voice names
    voice_map = {
        "ryan": "en_US-ryan-high",
        "amy": "en_US-amy-medium",
    }

    if specific_voice:
        if specific_voice in voice_map:
            specific_voice = voice_map[specific_voice]
        voices = [specific_voice]
    else:
        # Benchmark both default voices
        voices = ["en_US-ryan-high", "en_US-amy-medium"]

    print("=" * 60)
    print("  TTS Voice Synthesis Benchmark")
    print("  Playback: " + ("enabled" if play else "disabled"))
    print("=" * 60)

    results = []
    for voice in voices:
        result = benchmark_voice(voice, play=play)
        if result:
            results.append(result)
        time.sleep(0.5)  # Brief pause between voices

    # Comparison summary
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("  Comparison Summary")
        print("=" * 60)
        print(f"  {'Voice':<25} {'Avg Synth':<12} {'Min':<10} {'Max':<10}")
        print("  " + "-" * 58)
        for r in results:
            print(f"  {r['voice']:<25} {r['avg_synth_ms']:>8.0f} ms  "
                  f"{r['min_synth_ms']:>6.0f} ms  {r['max_synth_ms']:>6.0f} ms")

        # Quality vs Speed analysis
        print("\n  Analysis:")
        ryan = next((r for r in results if "ryan" in r["voice"]), None)
        amy = next((r for r in results if "amy" in r["voice"]), None)

        if ryan and amy:
            faster = ryan if ryan["avg_synth_ms"] < amy["avg_synth_ms"] else amy
            slower = amy if faster is ryan else ryan
            diff_pct = ((slower["avg_synth_ms"] - faster["avg_synth_ms"]) / slower["avg_synth_ms"]) * 100
            print(f"    • {faster['voice']} is {diff_pct:.0f}% faster ({faster['avg_synth_ms']:.0f}ms vs {slower['avg_synth_ms']:.0f}ms)")
            print(f"    • ryan-high: ~{ryan['avg_synth_ms']:.0f}ms avg (high quality)")
            print(f"    • amy-medium: ~{amy['avg_synth_ms']:.0f}ms avg (medium quality)")
            print("\n  Recommendation:")
            if faster["avg_synth_ms"] < 300:
                print(f"    {faster['voice']} meets <300ms target for confirmations")
            else:
                print("    Neither meets <300ms target — check if model is loaded in-process")

        print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
