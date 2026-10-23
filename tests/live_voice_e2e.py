"""LIVE end-to-end check of the real voice path (not part of the unit-test run).

Real Silero VAD, real Whisper, real Claude session, real tool registry +
policy executor, real TTS playback. Only the microphone is replaced by a WAV
script played in real time, and the orb click (tap to talk) is simulated by
setting the pipeline's listen_event just before the command arrives:

    silence -> [orb click] -> [command] -> silence

There is no wake word (removed 2026-10-18). Tools run in dry-run, and the
conversation goes to a temporary database. Jarvis WILL speak through the speakers.

    python tests/live_voice_e2e.py
    python tests/live_voice_e2e.py --command data/test_open_notepad_16k.wav --lead 8
"""
import argparse
import json
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core import paths  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--command", default=str(paths.cache_dir() / "test_open_notepad_16k.wav"))
    ap.add_argument("--lead", type=float, default=8.0, help="seconds of silence before the command")
    ap.add_argument("--click-before", type=float, default=0.5, help="seconds before the command the orb is clicked")
    args = ap.parse_args()

    import src.pipeline as pipeline
    from src.audio.file_source import FileAudioSource
    from src.memory.store import MemoryStore

    orig = pipeline.load_config

    def cfg():
        c = orig()
        c["dry_run"] = True
        return c
    pipeline.load_config = cfg

    source = FileAudioSource([(None, args.lead), (args.command, 4.0)])
    memory = MemoryStore(Path(tempfile.mkdtemp(prefix="jarvis-live-")) / "live.db")
    events, stop, ready = [], threading.Event(), threading.Event()
    listen = threading.Event()
    t_start, click = {}, {}

    def on_event(ev):
        ev = dict(ev, t=round(time.time() - t_start.get("t", time.time()), 2))
        events.append(ev)
        t = ev["type"]
        if t == "ready":
            t_start["t"] = time.time()
            ready.set()
            print(f"[READY] stt={ev.get('stt_model')} model={ev.get('model')} dry_run={ev.get('dry_run')}")
        elif t == "phase":
            print(f"  {ev['t']:6.2f}s phase: {ev['phase']}" + (f" ({ev.get('mode') or ev.get('tool') or ''})" if ev.get('mode') or ev.get('tool') else ""))
        elif t in ("transcript", "tool_call", "tool_result", "reply", "timing", "error", "info"):
            body = {k: v for k, v in ev.items() if k not in ("type", "t", "ts")}
            print(f"  {ev['t']:6.2f}s {t}: {json.dumps(body, default=str)[:220]}")
        if t == "timing" or (t == "error" and ev.get("stage") in ("llm", "stt")):
            stop.set()

    def click_orb():
        """Tap to talk: set listen_event shortly before the command reaches the (real-time) WAV mic."""
        if not ready.wait(240):
            return
        stream = source.last_stream
        if stream is None or stream._t0 is None:
            print("[CLICK] the audio stream never opened")
            return
        at = args.lead - args.click_before
        now = time.monotonic() - stream._t0
        if now > at:
            print(f"[CLICK] ready came {now:.1f}s into the WAV script, after the click point ({at:.1f}s); raise --lead")
            return
        time.sleep(at - now)
        click["t"] = time.monotonic() - stream._t0
        print(f"[CLICK] orb clicked at {click['t']:.2f}s of the WAV script")
        listen.set()

    th = threading.Thread(target=pipeline.main, kwargs=dict(
        event_callback=on_event, stop_event=threading.Event(), command_queue=queue.Queue(),
        memory=memory, session_id="live", components={"audio": source}, listen_event=listen), daemon=True)
    stop_event = th._kwargs["stop_event"]
    th.start()
    threading.Thread(target=click_orb, name="orb-click", daemon=True).start()
    finished = stop.wait(timeout=240 + source.duration_s)
    time.sleep(1.5)  # let the pipeline return to idle
    stop_event.set()
    th.join(30)
    pipeline.load_config = orig

    def seen(type_, **m):
        return any(e["type"] == type_ and all(e.get(k) == v for k, v in m.items()) for e in events)

    phases = [e["phase"] for e in events if e["type"] == "phase"]
    transcripts = [e["text"] for e in events if e["type"] == "transcript"]
    tools = [(e["tool"], e["status"]) for e in events if e["type"] == "tool_result"]
    replies = [e["text"] for e in events if e["type"] == "reply"]
    checks = {
        "orb click started listening": bool(click) and "listening" in phases,
        "no acknowledgement before listening": "listening" in phases and "speaking" not in phases[:phases.index("listening")],
        "Whisper produced a transcript": bool(transcripts),
        "Claude called a registry tool": bool(tools),
        "Jarvis spoke the reply": bool(replies),
        "returned to idle": "idle" in phases[phases.index("speaking") + 1:] if "speaking" in phases else False,
        "pipeline stopped cleanly": not th.is_alive() and seen("phase", phase="stopped"),
        "conversation stored": len(memory.recent_messages()) >= 2,
    }
    print("\nTranscript:", transcripts, "| tools:", tools, "| reply:", replies)
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = finished and all(checks.values())
    print("LIVE VOICE E2E:", "PASS" if ok else "FAIL")
    memory.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
