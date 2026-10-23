"""LIVE definition-of-done demo for the agentic task loop (not part of the unit-test run).

Objective (default): "Prepare the demo shop project for deployment, test it, fix any issues
you can safely fix, and tell me when it is ready."

sandbox/demo_shop starts broken: a real pricing bug (4 failing tests) and a missing VERSION
file that makes `python build.py` fail. One SIMULATED fault is injected (the first test run
"crashes") to prove recovery. Everything else is real: Claude Code session, tool registry,
PolicyExecutor, SQLite task state, independent verification, TTS.

    python tests/live_agent_demo.py            # typed objective through the real pipeline
    python tests/live_agent_demo.py --voice    # spoken: simulated orb click + Piper-synthesized objective -> real Whisper

Afterwards the script checks the outcome itself (tests + build run here, independently of
Jarvis) and prints the task timeline from data/jarvis.db.
"""
import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core import paths  # noqa: E402
DEMO = ROOT / "sandbox" / "demo_shop"
OBJECTIVE = ("Prepare the demo shop project for deployment, test it, fix any issues you can safely fix, "
             "and tell me when it is ready.")
FAULT = {"tool": "run_command", "match": "unittest", "times": 1, "transient": False,
         "message": "Test runner crashed before running any test: could not create a worker process (exit code -1)."}


def synth_wav(text: str, path: Path) -> Path:
    from src.tts import create_tts_engine
    tts = create_tts_engine()
    audio = tts.synthesize(text)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(tts.sample_rate)
        wf.writeframes(audio.tobytes())
    return path


def independent_check() -> dict:
    py = sys.executable
    t = subprocess.run([py, "-m", "unittest"], cwd=DEMO, capture_output=True, text=True)
    manifest = DEMO / "release" / "manifest.json"
    version = (DEMO / "VERSION").read_text().strip() if (DEMO / "VERSION").exists() else None
    m = json.loads(manifest.read_text()) if manifest.exists() else {}
    return {"tests_pass": t.returncode == 0, "version": version, "manifest_version": m.get("version")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", action="store_true")
    ap.add_argument("--objective", default=OBJECTIVE)
    ap.add_argument("--no-fault", action="store_true")
    args = ap.parse_args()

    subprocess.run([sys.executable, str(ROOT / "sandbox" / "reset_demo.py")], check=True)
    import src.pipeline as pipeline
    from src.memory.store import MemoryStore

    orig = pipeline.load_config

    def cfg():
        c = orig()
        c["dry_run"] = False
        c.setdefault("agent", {})["fault_injection"] = [] if args.no_fault else [FAULT]
        return c
    pipeline.load_config = cfg

    memory = MemoryStore()
    first_task = (memory.list_tasks(limit=1) or [{"id": 0}])[0]["id"]
    events, done = [], threading.Event()
    commands = queue.Queue()
    stop = threading.Event()
    components = {}
    listen = threading.Event()
    lead_s = 10.0                 # silence before the spoken objective; the orb is "clicked" 0.5 s before it
    if args.voice:
        tmp = paths.cache_dir() / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        from src.audio.file_source import FileAudioSource
        wav = synth_wav("Jarvis, " + args.objective, tmp / "objective.wav")
        components["audio"] = FileAudioSource([(None, lead_s), (str(wav), 4.0)])

    def click_orb():
        """Tap to talk (there is no wake word): set listen_event just before the objective plays."""
        stream = components["audio"].last_stream
        if stream is None or stream._t0 is None:
            print("CLICK: the audio stream never opened")
            return
        wait = (lead_s - 0.5) - (time.monotonic() - stream._t0)
        if wait < 0:
            print("CLICK: the pipeline became ready after the objective started; raise lead_s")
            return
        time.sleep(wait)
        listen.set()
    t0 = time.time()

    def on_event(ev):
        events.append(ev)
        t = ev["type"]
        el = f"{time.time() - t0:7.1f}s"
        if t == "ready":
            print(f"{el} READY ({ev.get('model')}, voice={ev.get('voice')})")
            if not args.voice:
                commands.put(args.objective)
            else:
                threading.Thread(target=click_orb, name="orb-click", daemon=True).start()
        elif t == "transcript":
            print(f"{el} YOU ({ev['source']}): {ev['text']}")
        elif t == "task_event":
            e = ev["event"]
            print(f"{el}   [task #{e['task_id']}] {e['kind']}: {e['message'][:150]}")
        elif t == "approval_request":
            print(f"{el}   APPROVAL REQUESTED: {ev['title']}  -> auto-denied by the demo script")
        elif t == "reply":
            print(f"{el} Jarvis (spoken): {ev['text']}")
        elif t == "error":
            print(f"{el} ERROR {ev.get('stage')}: {ev.get('detail')}")
        elif t == "timing":
            done.set()

    from src.tools.approvals import ApprovalBroker
    broker = ApprovalBroker()
    th = threading.Thread(target=pipeline.main, kwargs=dict(
        event_callback=on_event, stop_event=stop, command_queue=commands, memory=memory, session_id="demo",
        approval_broker=broker, components=components, listen_event=listen), daemon=True)
    th.start()
    # The demo must not need approvals (tests/builds are pre-approved for the demo project);
    # anything that asks is denied so nothing unexpected runs unattended.
    def deny_all():
        while not stop.is_set():
            if broker.pending is not None:
                broker.resolve(None, False, via="demo script")
            time.sleep(0.2)
    threading.Thread(target=deny_all, daemon=True).start()

    finished = done.wait(timeout=1500)
    time.sleep(1.0)
    stop.set()
    th.join(30)
    pipeline.load_config = orig

    tasks = [t for t in memory.list_tasks(limit=10) if t["id"] > first_task]
    print("\n" + "=" * 70)
    if not tasks:
        print("No task was created.")
        return 1
    task = tasks[-1]
    evs = memory.task_events(task["id"])
    print(f"Task #{task['id']} [{task['status']}] {task['objective']}")
    print("Plan:")
    for s in task.get("plan") or []:
        print(f"  {s['id']}. [{s.get('status')}] {s['title']}")
    print("Success checks:")
    for r in task.get("verification") or []:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['description']}")
    kinds = [e["kind"] for e in evs]
    sim = [e for e in evs if "simulated fault" in e["message"]]
    after_sim = sim and any(e["kind"] == "tool_ok" and "run_command" in e["message"] and e["id"] > sim[0]["id"] for e in evs)
    indep = independent_check()
    replies = [e["text"] for e in events if e["type"] == "reply"]
    checks = {
        "objective accepted as a tracked task": "accepted" in kinds,
        "environment inspected before planning": any(e["kind"] == "tool" for e in evs[:kinds.index("plan")]) if "plan" in kinds else False,
        "plan created": "plan" in kinds,
        "multiple authorized actions executed": sum(1 for k in kinds if k == "tool_ok") >= 4,
        "simulated failure observed": bool(sim) or args.no_fault,
        "recovered after the failure (later successful run)": bool(after_sim) or args.no_fault,
        "independent verification passed": task["status"] == "COMPLETED" and all(r["passed"] for r in task.get("verification") or []),
        "outcome confirmed outside Jarvis (tests pass, VERSION 1.0.0, manifest built)":
            indep["tests_pass"] and indep["version"] == "1.0.0" and indep["manifest_version"] == "1.0.0",
        "final report spoken": bool(replies),
        "voice input used" if args.voice else "typed input used": any(e["type"] == "transcript" and e["source"] == ("voice" if args.voice else "text") for e in events),
    }
    print("Independent check:", indep)
    print("Spoken report:", replies[-1] if replies else None)
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = finished and all(checks.values())
    print("LIVE AGENT DEMO:", "PASS" if ok else "FAIL")
    memory.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
