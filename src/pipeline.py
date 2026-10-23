"""Jarvis voice pipeline — production entry point.

Orb click (VAD capture), push-to-talk or a typed command → STT →
Agent (the configured AI + tool registry via PolicyExecutor) → TTS → idle.
There is no wake word: the floating orb is always on screen. Opening the window
queues a greeting (src/agent/greetings.py), spoken here like a reply.

Error handling:
  - Each stage catches its own exceptions and logs them
  - Individual command failures return to idle, never crash the loop
  - Audio device errors attempt reconnect
  - Config validation at startup (only what cannot degrade: the AI connection, llm.main); a missing
    microphone, speech model or voice falls back to text-only / no speech instead
  - Every command cycle logged to command_log.jsonl in the cache folder
"""
import sys
import json
import time
import signal
import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import config_store, paths  # noqa: E402

# Pipeline states (used by tray app for icon updates)
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_PROCESSING = "processing"
STATE_SPEAKING = "speaking"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"

stop_flag = False

# Command sources answered on another device (the reply is sent there, not spoken here).
REMOTE_SOURCES = {"phone"}


def strip_address(text: str, name: str = "") -> str:
    """"Nova, open chrome" -> "open chrome": addressing the assistant (by its name, or the product name
    Jarvis) is not part of the request."""
    import re
    names = sorted({"jarvis", (name or "").lower()} - {""}, key=len, reverse=True)
    alternatives = "|".join(re.escape(n) for n in names)
    return re.sub(rf"^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?(?:{alternatives})\b[\s,.!?:-]*", "", text, flags=re.I)


def signal_handler(sig, frame):
    global stop_flag
    print("\n\nStopping...")
    stop_flag = True


import threading
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, signal_handler)


def load_config():
    """The settings in effect (tests replace this function with a fixed config)."""
    return config_store.load_config()


def validate_config(cfg: dict) -> list:
    """Return a list of config errors that stop the pipeline. Empty means it can start.

    Only the brain (llm.main) is required: the Claude Code CLI for Claude, or a model, server address and
    (where the provider needs one) an API key. The microphone (null = the system default), the speech
    model and the voice are checked where they load: if one is missing the pipeline runs text-only or
    silent and reports why, instead of not starting at all.
    """
    from src.agent.providers import problems
    return problems(cfg, "main")


def report_vram(label: str):
    torch = sys.modules.get("torch")            # only when something else loaded it (the core doesn't need torch)
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            alloc = torch.cuda.memory_allocated() / 1024**2
            res = torch.cuda.memory_reserved() / 1024**2
            total = torch.cuda.get_device_properties(0).total_memory / 1024**2
            print(f"[VRAM] {label}: {alloc:.0f}MB alloc, {res:.0f}MB reserved, {total - res:.0f}MB free / {total:.0f}MB")
    except Exception:
        pass


def log_command(entry: dict):
    """Append a command log entry to command_log.jsonl in the cache folder."""
    entry["timestamp"] = datetime.datetime.now().isoformat()
    try:
        log = paths.cache_dir() / "command_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def listen_for_confirmation(stream, chunk_size, sample_rate,
                            chunk_has_speech, transcriber, cfg):
    """Capture a spoken yes/no reply to an approval prompt. Returns lowercase text."""
    window_ms = cfg.get("voice_approval", {}).get("window_ms", 5000)

    chunk_duration_s = chunk_size / sample_rate
    window_chunks = int((window_ms / 1000.0) / chunk_duration_s)
    silence_limit = int((400 / 1000.0) / chunk_duration_s)

    speech_chunks = []
    silent_chunks = 0
    heard_speech = False
    chunks_recorded = 0

    while chunks_recorded < window_chunks:
        try:
            chunk, overflow = stream.read(chunk_size)
        except Exception as e:
            print(f"[CONFIRM] Audio read error: {e}")
            return ""
        audio_int16 = np.frombuffer(chunk, dtype=np.int16)
        chunks_recorded += 1

        has_speech = chunk_has_speech(audio_int16)
        if has_speech:
            speech_chunks.append(audio_int16)
            heard_speech = True
            silent_chunks = 0
        elif heard_speech:
            silent_chunks += 1
            if silent_chunks <= 2:
                speech_chunks.append(audio_int16)

        if heard_speech and silent_chunks >= silence_limit:
            break

    if not heard_speech or not speech_chunks:
        print("[CONFIRM] No speech detected in confirmation window")
        return ""

    speech_audio = np.concatenate(speech_chunks)
    try:
        text = transcriber.transcribe_audio(speech_audio)
        return text.strip().lower()
    except Exception as e:
        print(f"[CONFIRM] STT error: {e}")
        return ""


def tts_speak(tts, text: str, speaking_flag: list, state_cb=None) -> dict:
    """Speak with anti-self-trigger. Returns timing dict or empty."""
    if tts is None:
        return {}
    try:
        speaking_flag[0] = True
        if state_cb:
            state_cb(STATE_SPEAKING)
        result = tts.speak(text, blocking=True)
        speaking_flag[0] = False
        if state_cb:
            state_cb(STATE_PROCESSING)
        time.sleep(0.3)
        return result or {}
    except Exception as e:
        print(f"[TTS ERROR] {e}")
        speaking_flag[0] = False
        return {}


def speakable(text: str, max_chars: int = 450) -> str:
    """Plain speech from a reply: no markdown, code or bullets; long reports are cut at a sentence
    boundary (the full text stays on screen)."""
    import re
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\*\*|__|\*|#+ ", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + ".") + " The details are on screen."


def drain_input(stream, chunk_size: int) -> int:
    """Discard audio buffered in the input stream (e.g. Jarvis's own voice
    recorded while TTS was playing). Returns the number of chunks dropped."""
    dropped = 0
    try:
        available = stream.read_available
        while available >= chunk_size:
            stream.read(chunk_size)
            available -= chunk_size
            dropped += 1
    except Exception:
        pass
    return dropped


def main(state_callback=None, stop_event=None, duration=None,
         event_callback=None, command_queue=None, pause_event=None,
         cancel_event=None, ptt_event=None, approval_broker=None,
         memory=None, session_id=None, components=None, listen_event=None, interrupt_event=None):
    """Run the Jarvis voice pipeline.

    Args:
        state_callback: Optional callable(state_str) for tray/HUD state updates
            (idle | listening | processing | speaking | error | stopped).
        stop_event: Optional threading.Event — pipeline stops when set.
        duration: Optional int seconds to run (0 or None = forever).
        event_callback: Optional callable(dict) receiving structured events for UIs.
            Event types: ready, phase, transcript, action, tool_call, tool_result,
            result, approval_request, approval_resolved, reply, timing, error,
            info, cancelled.
        command_queue: Optional queue.Queue of typed text commands (UI text input).
            Typed commands skip capture / STT and go straight to the agent.
            If a microphone is unavailable and a queue is given, the pipeline runs
            in text-only mode instead of exiting.
        pause_event: Optional threading.Event — microphone muted: while set, orb clicks
            and push-to-talk do not record (typed commands still work).
        cancel_event: Optional threading.Event — set to abort the command in
            progress (capture, Claude, tools, speech). Cleared when the pipeline
            returns to idle.
        ptt_event: Optional threading.Event — push-to-talk: while set, audio is
            recorded as a command.
        interrupt_event: Optional threading.Event — stops the running Claude turn without
            cancelling the request (used to pause a task; the task decides what happens).
        command_queue may also carry dicts {"type": "task", "action": "resume"|"retry",
            "task_id": int, "answer": str} from the GUI's agent workspace, and
            {"type": "greet"} when the window opens (speak a new greeting).
        listen_event: Optional threading.Event — "tap to talk" (orb click): when set
            while idle, capture one command with VAD. Cleared by the pipeline.
        approval_broker: Optional ApprovalBroker shared with the UI, so approval
            prompts can be answered from buttons/keys as well as by voice.
        memory: Optional MemoryStore (defaults to jarvis.db in the user's data folder).
        session_id: Optional id stored with conversation rows.
        components: Optional dict of pre-built stages (tests / file audio):
            audio, transcriber, vad, tts, agent, router, executor, stop_playback, greeter.
    """
    import queue as _queue
    import contextlib
    import threading
    import uuid
    from src.core.config_store import load_config as load_full_config
    from src.memory.store import MemoryStore
    from src.router.action_router import ActionRouter
    from src.tools.approvals import ApprovalBroker, classify_reply
    from src.tools.policy import PolicyExecutor
    from src.tools.registry import ToolContext, build_default_registry

    components = components or {}
    session_id = session_id or uuid.uuid4().hex[:12]
    cancel_event = cancel_event or threading.Event()

    def set_state(state):
        if state_callback:
            try:
                state_callback(state)
            except Exception:
                pass

    def emit(**event):
        if event_callback:
            try:
                event_callback(event)
            except Exception:
                pass

    def phase(name, **extra):
        emit(type="phase", phase=name, **extra)

    def should_stop():
        if stop_event and stop_event.is_set():
            return True
        return stop_flag

    def paused():
        return bool(pause_event and pause_event.is_set())

    def ptt_down():
        return bool(ptt_event and ptt_event.is_set())

    if duration is None:
        duration = 0
        for i, arg in enumerate(sys.argv):
            if arg == "--duration" and i + 1 < len(sys.argv):
                duration = int(sys.argv[i + 1])

    # --- Config validation ---
    cfg = load_config()
    full_cfg = load_full_config()
    for k, v in full_cfg.items():  # Phase 2 defaults (agent, tools, memory, ...) without overriding test patches
        cfg.setdefault(k, v)
        if isinstance(v, dict) and isinstance(cfg[k], dict):
            for kk, vv in v.items():
                cfg[k].setdefault(kk, vv)
    errors = [] if components.get("skip_validation") else validate_config(cfg)
    if errors:
        print("CONFIG ERRORS:")
        for e in errors:
            print(f"  - {e}")
            emit(type="error", stage="config", detail=e)
        return 1

    pipe_cfg = cfg.get("pipeline", {})
    silence_timeout_ms = pipe_cfg.get("silence_timeout_ms", 400)
    max_recording_ms = pipe_cfg.get("max_recording_ms", 8000)
    early_bail_ms = pipe_cfg.get("early_bail_ms", 2000)
    vad_threshold = pipe_cfg.get("vad_threshold", 0.5)

    print("=" * 70)
    print(f"  {config_store.assistant_name(cfg)} — Voice Assistant")
    if duration > 0:
        print(f"  Duration: {duration}s | Press Ctrl+C to stop")
    else:
        print(f"  Press Ctrl+C to stop")
    print(f"  Silence cutoff: {silence_timeout_ms}ms | Max recording: {max_recording_ms}ms")
    print("=" * 70)

    report_vram("Before init")

    # --- Voice stages (mic, STT, VAD). Degrade to text-only if missing. ---
    audio = components.get("audio")
    input_stream = None
    transcriber = components.get("transcriber")
    vad_fn = components.get("vad")
    voice_ok = True
    vad_model = None
    try:
        if audio is None:
            from src.audio.capture import AudioCapture
            print("\n[INIT] Loading audio capture...")
            audio = AudioCapture()
        if transcriber is None:
            from src.stt.transcriber import Transcriber
            print("[INIT] Loading STT (whisper + VAD)...")
            transcriber = Transcriber()
            transcriber.load()
        if vad_fn is None:
            vad_model, _ = transcriber._load_vad()
        # Open the input stream now, so a missing/unplugged microphone degrades to
        # text-only mode here instead of crashing the loop later.
        print("[INIT] Opening microphone...")
        input_stream = audio.stream()
        report_vram("After STT")
    except Exception as e:
        if command_queue is None:
            print(f"[FATAL] Voice stage failed: {e}")
            emit(type="error", stage="voice", detail=str(e))
            return 1
        voice_ok = False
        print(f"[WARN] Voice input unavailable ({e}) — running in TEXT-ONLY mode")
        emit(type="error", stage="voice", detail=f"Voice input unavailable: {e}. Text-only mode.")

    # --- Router, memory, tools, policy, approvals ---
    print("[INIT] Loading router, tools and memory...")
    router = components.get("router") or ActionRouter()
    router.dry_run = cfg.get("dry_run", True)
    if router.dry_run:
        print("[WARN] dry_run=true — commands will be logged but not executed")
    if memory is None:
        try:
            memory = MemoryStore()
        except Exception as e:
            print(f"[WARN] Memory store unavailable (continuing without): {e}")
            emit(type="error", stage="memory", detail=f"Memory unavailable: {e}")
            memory = None
    if memory is not None:
        # Anything still "running" belongs to a previous process that stopped mid-way.
        memory.mark_interrupted_runs()
        for t in memory.reconcile_after_restart():
            emit(type="info", detail=f"Task #{t['id']} “{t['objective']}” was interrupted; resume it from the Agent tab.")
    broker = approval_broker or ApprovalBroker()
    task_manager = None

    def broker_emit(ev):
        emit(**ev)
        if task_manager is not None:
            task_manager.observe(ev)
    broker.set_emitter(broker_emit)

    tool_ms = {"total": 0.0}

    def on_tool_event(ev):
        emit(**ev)
        if task_manager is not None:
            task_manager.observe(ev)
        if ev["type"] == "tool_call":
            phase("executing", tool=ev["tool"], purpose=ev.get("purpose", ""))
        elif ev["type"] == "tool_result":
            tool_ms["total"] += ev.get("ms", 0)
            phase("thinking")

    executor = components.get("executor")
    if executor is None:
        executor = PolicyExecutor(build_default_registry(router, memory, cfg), lambda: cfg, broker, memory, on_tool_event)
    else:
        executor.set_emitter(on_tool_event)
        if executor.approvals is None:
            executor.approvals = broker

    # --- Task layer: planning tools + verification (must exist before Claude connects) ---
    if memory is not None:
        from src.agent.tasks import TaskManager
        task_manager = TaskManager(memory, executor, lambda: cfg, lambda ev: emit(**ev))
        if executor.registry.get("create_plan") is None:
            task_manager.register_tools(executor.registry)

    # --- Agent (the AI + tools) wrapped by the orchestrator ---
    print("[INIT] Loading agent...")
    agent = components.get("agent")
    try:
        if agent is None:
            from src.agent import create_agent
            agent = create_agent(cfg, executor, memory)
        if task_manager is not None:
            from src.agent.orchestrator import Orchestrator
            agent = Orchestrator(agent, task_manager, lambda: cfg)
        agent.load()
    except Exception as e:
        print(f"[FATAL] Agent failed: {e}")
        emit(type="error", stage="llm", detail=str(e))
        return 1
    report_vram("After LLM")

    print("[INIT] Loading TTS engine...")
    tts = components.get("tts")
    if tts is None and cfg.get("tts", {}).get("enabled", False):
        try:
            from src.tts import create_tts_engine
            tts = create_tts_engine()
        except Exception as e:
            print(f"[WARN] TTS failed to load (continuing without voice): {e}")
            emit(type="error", stage="tts", detail=f"TTS unavailable: {e}")

    stop_playback = components.get("stop_playback")
    if stop_playback is None:
        def stop_playback():
            try:
                if hasattr(tts, "stop"):
                    tts.stop()           # also drops sentences still queued in the Chatterbox worker
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass

    report_vram("After all init")
    print("[INIT] All components loaded\n")
    set_state(STATE_IDLE)
    emit(
        type="ready",
        backend=getattr(agent, "name", ""),
        model=getattr(agent, "model", ""),
        voice=voice_ok,
        tts=tts is not None,
        tts_voice=(getattr(tts, "voice_name", None) or cfg.get("tts", {}).get("piper", {}).get("voice")) if tts else None,
        stt_model=cfg.get("stt", {}).get("model_path", "").split("/")[-1] if voice_ok else None,
        stt_device=cfg.get("stt", {}).get("device") if voice_ok else None,
        dry_run=router.dry_run,
        tools=executor.registry.names(),
        session_id=session_id,
    )
    phase("idle")

    # --- Pipeline constants ---
    if voice_ok:
        sample_rate = audio.sample_rate
        chunk_size = audio.chunk_size
        chunk_duration_s = chunk_size / sample_rate
        silence_chunks = int((silence_timeout_ms / 1000.0) / chunk_duration_s)
        max_chunks = int((max_recording_ms / 1000.0) / chunk_duration_s)
        early_bail_chunks = int((early_bail_ms / 1000.0) / chunk_duration_s)
    else:
        sample_rate = chunk_size = 0
        chunk_duration_s = 0.032

    vad_window = 512

    def chunk_has_speech(audio_int16):
        if vad_fn is not None:
            return vad_fn(audio_int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        for start in range(0, len(audio_float) - vad_window + 1, vad_window):
            if vad_model(audio_float[start:start + vad_window], sample_rate) > vad_threshold:
                return True
        return False

    level_state = {"t": 0.0}

    def emit_level(audio_int16):
        # Mic level for the orb (~12 updates/s while listening).
        now = time.time()
        if now - level_state["t"] < 0.08:
            return
        level_state["t"] = now
        rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2))) if len(audio_int16) else 0.0
        emit(type="level", v=round(min(1.0, rms / 2500.0), 3))

    # After every utterance the input buffer is drained, so Jarvis's own voice
    # (recorded while playback blocked) never becomes part of the next command.
    speaking = [False]
    stream_box = {"stream": None}
    stream_lock = threading.RLock()

    def after_speech():
        stream = stream_box["stream"]
        if stream is not None:
            with stream_lock:
                drain_input(stream, chunk_size)

    # The command being processed: where it came from (voice | text | phone) and the front-end's
    # reference, so a remote front-end can match the reply events to its own message.
    current = {"source": None, "ref": None}

    def origin() -> dict:
        return {"source": current["source"], "ref": current["ref"]} if current["ref"] else {}

    def remote_quiet() -> bool:
        """A phone command is answered in text; the PC speakers stay silent unless the user wants both."""
        return current["source"] in REMOTE_SOURCES and not cfg.get("phone", {}).get("speak_replies", False)

    def speak(text, record=True):
        if not text or cancel_event.is_set():
            return {}
        if record:
            emit(type="reply", text=text, **origin())
        if remote_quiet():
            return {}
        spoken = speakable(text, pipe_cfg.get("max_spoken_chars", 450))
        phase("speaking", text=spoken)
        result = tts_speak(tts, spoken, speaking, set_state)
        after_speech()
        return result

    # --- Approval prompts: speak the question, accept spoken or typed yes/no ---
    voice_cfg = cfg.get("voice_approval", {})
    affirmative = voice_cfg.get("affirmative", ["yes"]) + ["allow", "approve", "approved", "y"]
    negative = voice_cfg.get("negative", ["no"]) + ["deny", "denied", "n"]

    def approval_prompter(req):
        phase("approval", tool=req.tool, title=req.title)
        remote = remote_quiet()
        if not remote:          # a phone command gets the question as Approve/Deny buttons instead
            speak(req.spoken, record=True)
        deferred = []
        window_end = 0 if remote else time.time() + voice_cfg.get("window_ms", 5000) / 1000.0
        try:
            while not req.resolved and time.time() < req.deadline and not cancel_event.is_set():
                # Typed yes/no from front-ends that only have the command queue.
                if command_queue is not None:
                    try:
                        typed = command_queue.get_nowait()
                        if not isinstance(typed, str):
                            deferred.append(typed)
                            raise _queue.Empty
                        verdict = classify_reply(typed, affirmative, negative)
                        if verdict in ("yes", "no"):
                            emit(type="transcript", text=typed, source="text")
                            broker.resolve(req.id, verdict == "yes", via=f"typed '{typed}'")
                            break
                        deferred.append(typed)
                    except _queue.Empty:
                        pass
                stream = stream_box["stream"]
                if (stream is not None and voice_ok and not paused() and time.time() < window_end):
                    with stream_lock:
                        reply_text = listen_for_confirmation(stream, chunk_size, sample_rate,
                                                             chunk_has_speech, transcriber, cfg)
                    window_end = 0  # one spoken answer per prompt
                    if reply_text:
                        verdict = classify_reply(reply_text, affirmative, negative)
                        emit(type="transcript", text=reply_text, source="voice")
                        emit(type="info", detail=f"Confirmation: '{reply_text}' -> {verdict}")
                        if verdict in ("yes", "no"):
                            broker.resolve(req.id, verdict == "yes", via=f"voice '{reply_text}'")
                            break
                else:
                    time.sleep(0.05)
        finally:
            for item in deferred:
                command_queue.put(item)
        phase("executing", tool=req.tool)

    broker.prompter = approval_prompter
    if task_manager is not None:
        task_manager.speaker = lambda text: speak(text, record=False)

    def remember_message(role, text, source=None, meta=None):
        if memory is None or not text:
            return
        try:
            memory.add_message(session_id, role, text, source=source, meta=meta)
        except Exception as e:
            print(f"[WARN] Could not store message: {e}")

    def process_command(text, source, t_start, capture_ms, stt_ms, runner=None, ref=None):
        """Agent (AI + tools) -> spoken reply -> history -> log, for one transcript."""
        current.update(source=source, ref=ref)
        try:
            run_command(text, source, t_start, capture_ms, stt_ms, runner)
        finally:
            current.update(source=None, ref=None)

    def run_command(text, source, t_start, capture_ms, stt_ms, runner):
        emit(type="transcript", text=text, source=source, ref=current["ref"])
        remember_message("user", text, source=source)
        set_state(STATE_PROCESSING)
        phase("thinking")
        tool_ms["total"] = 0.0

        ctx = ToolContext(session_id=session_id, source=source, cancel_event=cancel_event)
        ctx.interrupt_event = interrupt_event
        if interrupt_event is not None:
            interrupt_event.clear()
        t_llm_start = time.time()
        try:
            if runner is not None:
                result = runner(ctx)
            else:
                result = agent.handle(text, source, ctx, lambda ev: emit(**ev))
        except Exception as e:
            result = None
            err = str(e)
        agent_ms = (time.time() - t_llm_start) * 1000
        exec_ms = tool_ms["total"]
        llm_ms = max(0.0, agent_ms - exec_ms)

        if result is None or (not result.ok and not result.cancelled):
            detail = err if result is None else (result.error or "unknown error")
            print(f"[ERROR] Agent failed ({agent_ms:.0f}ms): {detail}")
            emit(type="error", stage=(result.stage if result else "llm"), detail=detail, **origin())
            remember_message("error", detail, meta={"stage": "llm"})
            if result is not None and result.spoken:
                speak(result.spoken)
            else:
                speak("Sorry, I couldn't reach Claude" if "Claude" in detail else
                      "Sorry, I couldn't reach the AI" if result is None else "Sorry, something went wrong")
            log_command({"text": text, "source": source, "error": "agent_failed", "detail": detail})
            return
        if result.cancelled or cancel_event.is_set():
            print(f"[CANCEL] Command cancelled after {agent_ms:.0f}ms")
            emit(type="cancelled", detail="Cancelled", tools=[c.get("tool") for c in result.tool_calls], **origin())
            remember_message("system", "Cancelled", meta={"tools": result.tool_calls})
            log_command({"text": text, "source": source, "cancelled": True, "tools": result.tool_calls})
            return

        print(f"[AGENT] {len(result.tool_calls)} tool call(s), {result.turns} turn(s), {agent_ms:.0f}ms: {result.reply!r}")
        tts_result = speak(result.reply) if result.reply else {}
        remember_message("assistant", result.reply, meta={"tools": result.tool_calls, "task_id": result.task_id})
        tts_total = (tts_result.get("synthesis_time", 0) + tts_result.get("playback_time", 0)) * 1000

        total_ms = (time.time() - t_start) * 1000
        print(f"\n[TIMING] Capture: {capture_ms:.0f}ms | STT: {stt_ms:.0f}ms | "
              f"LLM: {llm_ms:.0f}ms | Exec: {exec_ms:.0f}ms | "
              f"TTS: {tts_total:.0f}ms | TOTAL: {total_ms:.0f}ms")
        timing = {
            "capture_ms": round(capture_ms),
            "stt_ms": round(stt_ms),
            "llm_ms": round(llm_ms),
            "exec_ms": round(exec_ms),
            "tts_ms": round(tts_total),
            "total_ms": round(total_ms),
            "source": source,
        }
        emit(type="timing", ref=current["ref"], **timing)
        log_command({
            "text": text,
            "source": source,
            "reply": result.reply,
            "tools": result.tool_calls,
            "timing": timing,
        })

    def run_task_command(cmd):
        """GUI agent workspace: resume / retry a task through the same path as a spoken request."""
        tid = int(cmd.get("task_id", 0))
        action = cmd.get("action")
        if not hasattr(agent, "resume"):
            emit(type="error", stage="task", detail="Task control needs the memory store")
            return
        label = f"Resume task #{tid}" if action == "resume" else f"Retry task #{tid}"
        if action == "resume":
            runner = lambda ctx: agent.resume(tid, ctx, lambda ev: emit(**ev), cmd.get("answer", ""))
        elif action == "retry":
            runner = lambda ctx: agent.retry(tid, ctx, lambda ev: emit(**ev))
        else:
            emit(type="error", stage="task", detail=f"Unknown task action {action!r}")
            return
        process_command(label, "text", time.time(), 0, 0, runner=runner)

    def pop_typed_command():
        if command_queue is None:
            return None
        try:
            return command_queue.get_nowait()
        except _queue.Empty:
            return None

    def finish_cycle():
        """Return to idle: clear a cancel, release speech state, drop buffered audio."""
        if cancel_event.is_set():
            stop_playback()
            cancel_event.clear()
        speaking[0] = False
        after_speech()
        idle_since[0] = time.time()
        set_state(STATE_IDLE)
        phase("idle")

    def transcribe_and_process(buffer, flags, t_start, capture_ms):
        parts = []
        for i, (chunk_data, is_speech) in enumerate(zip(buffer, flags)):
            if is_speech or (i > 0 and flags[i - 1]) or (i < len(flags) - 1 and flags[i + 1]):
                parts.append(chunk_data)
        if not parts:
            print("[VAD] No speech chunks")
            emit(type="info", detail="No speech heard")
            return
        speech_audio = np.concatenate(parts)
        speech_duration = len(speech_audio) / sample_rate
        set_state(STATE_PROCESSING)
        phase("transcribing")
        t_stt_start = time.time()
        try:
            text = transcriber.transcribe_audio(speech_audio)
        except Exception as e:
            print(f"[ERROR] STT failed: {e}")
            emit(type="error", stage="stt", detail=f"Transcription failed: {e}")
            log_command({"error": "stt_failed", "detail": str(e)})
            return
        stt_ms = (time.time() - t_stt_start) * 1000
        rtf = (stt_ms / 1000.0) / speech_duration if speech_duration > 0 else 0
        print(f"[STT] '{text}' ({stt_ms:.0f}ms, RTF={rtf:.2f}x)")
        text = strip_address(text, config_store.assistant_name(cfg))
        if not text.strip():
            print("[STT] Empty transcript")
            emit(type="info", detail="Heard speech but the transcript was empty")
            return
        if cancel_event.is_set():
            return
        process_command(text.strip(), "voice", t_start, capture_ms, stt_ms)

    def run_remote_command(cmd):
        """A command from a remote front-end (the phone app): text, or a voice recording transcribed here
        with the same Whisper model (one at a time, so no second model on the GPU and no lock)."""
        source, ref = cmd.get("source") or "phone", cmd.get("ref") or None
        t0 = time.time()
        text, stt_ms = (cmd.get("text") or "").strip(), 0.0
        if cmd.get("audio"):
            phase("transcribing")
            try:
                if transcriber is None:
                    raise RuntimeError("speech recognition is not loaded")
                from src.stt.transcriber import load_audio_file
                text = strip_address(transcriber.transcribe_audio(load_audio_file(cmd["audio"])),
                                     config_store.assistant_name(cfg)).strip()
            except Exception as e:
                print(f"[ERROR] Voice note STT failed: {e}")
                emit(type="error", stage="stt", detail=f"Could not transcribe the voice note: {e}", source=source, ref=ref)
                return
            stt_ms = (time.time() - t0) * 1000
            print(f"[STT] voice note '{text}' ({stt_ms:.0f}ms)")
            if not text:
                emit(type="error", stage="stt", detail="The voice note had no words in it", source=source, ref=ref)
                return
        if not text:
            return
        print(f"\n[REMOTE] {source} '{text}'")
        process_command(text, source, t0, 0, stt_ms, ref=ref)

    def capture_command(stream):
        """VAD capture of one spoken command after an orb click. Returns (buffer, flags, heard)."""
        bail = early_bail_chunks
        buffer, flags = [], []
        silent = 0
        heard = False
        recorded = 0
        phase("listening", mode="tap")
        while not should_stop() and not cancel_event.is_set() and recorded < max_chunks:
            try:
                with stream_lock:
                    chunk, overflow = stream.read(chunk_size)
            except Exception as e:
                print(f"[ERROR] Audio read in capture: {e}")
                break
            audio_int16 = np.frombuffer(chunk, dtype=np.int16)
            buffer.append(audio_int16)
            recorded += 1
            emit_level(audio_int16)
            has_speech = chunk_has_speech(audio_int16)
            flags.append(has_speech)
            if has_speech:
                heard = True
                silent = 0
            elif heard:
                silent += 1
            if not heard and recorded >= bail:
                break
            if heard and silent >= silence_chunks:
                break
        return buffer, flags, heard

    def capture_push_to_talk(stream):
        """Record while push-to-talk is held (capped at max_recording_ms)."""
        buffer, flags = [], []
        phase("listening", mode="ptt")
        recorded = 0
        while ptt_down() and not should_stop() and not cancel_event.is_set() and recorded < max_chunks:
            try:
                with stream_lock:
                    chunk, overflow = stream.read(chunk_size)
            except Exception as e:
                print(f"[ERROR] Audio read in push-to-talk: {e}")
                break
            audio_int16 = np.frombuffer(chunk, dtype=np.int16)
            buffer.append(audio_int16)
            emit_level(audio_int16)
            flags.append(chunk_has_speech(audio_int16))
            recorded += 1
        return buffer, flags, any(flags)

    # --- Window greetings: a new one each time the window opens (queued by the controller) ---
    greeter = components.get("greeter")
    if greeter is None and cfg.get("greeting", {}).get("enabled", True):
        try:
            from src.agent.greetings import GreetingPool
            from src.agent.usage import UsageMeter
            from src.agent.utility import utility_generator
            generator = utility_generator(cfg, UsageMeter(memory, lambda: cfg),
                                          timeout_s=float(cfg.get("greeting", {}).get("timeout_s", 120)))
            greeter = GreetingPool(cfg, memory, tts, profile=getattr(router, "profile", None), generator=generator)
            greeter.refill_async()
        except Exception as e:
            print(f"[WARN] Greetings unavailable: {e}")
            greeter = None
    idle_since = [time.time()]

    def greet():
        if greeter is None:
            return
        try:
            text = greeter.take()
        except Exception as e:
            print(f"[GREETING] {e}")
            return
        print(f"[GREETING] {text!r}")
        emit(type="reply", text=text, greeting=True)
        speak(text, record=False)
        if hasattr(tts, "discard"):
            tts.discard(text)

    def idle_tick():
        """While idle, pre-render the next greeting once nothing has happened for a while
        (not right away: the voice worker should be free for the reply to the next command)."""
        if greeter is None or speaking[0]:
            return
        if time.time() - idle_since[0] >= float(cfg.get("greeting", {}).get("prepare_after_idle_s", 20)):
            idle_since[0] = time.time()
            try:
                if greeter.needs_prepare:
                    greeter.prepare_next()
            except Exception as e:
                print(f"[GREETING] Could not prepare the next greeting: {e}")

    mode = "Listening" if voice_ok else "Text-only (no microphone)"
    print(f"[READY] {mode}...\n" + "-" * 70)

    start_time = time.time()
    command_count = 0
    exit_code = 0
    read_failures = 0

    try:
        stream_ctx = input_stream if voice_ok else contextlib.nullcontext(None)
        with stream_ctx as stream:
            stream_box["stream"] = stream
            while not should_stop():
                if duration > 0 and (time.time() - start_time) > duration:
                    break

                # --- Typed command from the UI ---
                typed = pop_typed_command()
                if isinstance(typed, dict) and typed.get("type") == "greet":
                    greet()
                    finish_cycle()
                    continue
                if isinstance(typed, dict) and typed.get("type") == "remote":
                    command_count += 1
                    run_remote_command(typed)
                    finish_cycle()
                    print("-" * 70)
                    continue
                if isinstance(typed, dict):
                    run_task_command(typed)
                    finish_cycle()
                    continue
                if typed is not None and typed.strip():
                    command_count += 1
                    t0 = time.time()
                    print(f"\n[TEXT] #{command_count} '{typed.strip()}'")
                    process_command(typed.strip(), "text", t0, 0, 0)
                    finish_cycle()
                    print("-" * 70)
                    continue

                if cancel_event.is_set():
                    cancel_event.clear()

                if not voice_ok:
                    idle_tick()
                    time.sleep(0.05)
                    continue

                # --- Push-to-talk ---
                if ptt_down() and not paused():
                    command_count += 1
                    t_start = time.time()
                    print(f"\n[PTT] #{command_count} recording...")
                    set_state(STATE_LISTENING)
                    buffer, flags, heard = capture_push_to_talk(stream)
                    capture_ms = (time.time() - t_start) * 1000
                    if heard:
                        print(f"[PTT] {capture_ms:.0f}ms captured")
                        transcribe_and_process(buffer, flags, t_start, capture_ms)
                    else:
                        emit(type="info", detail="Push-to-talk: no speech heard")
                    finish_cycle()
                    print("-" * 70)
                    continue

                try:
                    with stream_lock:
                        chunk, overflow = stream.read(chunk_size)
                    read_failures = 0
                except Exception as e:
                    read_failures += 1
                    print(f"[ERROR] Audio read failed: {e}")
                    if read_failures == 1:
                        emit(type="error", stage="audio", detail=f"Microphone read failed: {e}. Typed commands still work; "
                                                                  "restart the assistant after reconnecting the mic.")
                    time.sleep(0.5)
                    continue

                # Idle: the chunk is read only to keep the stream fresh. An orb click
                # starts one command; the buffer is drained so it holds only new speech.
                if not (listen_event and listen_event.is_set()):
                    idle_tick()
                    continue
                listen_event.clear()
                if paused():
                    emit(type="info", detail="Microphone is muted")
                    continue
                command_count += 1
                set_state(STATE_LISTENING)
                print(f"\n[LISTEN] #{command_count} (orb click)")
                with stream_lock:
                    drain_input(stream, chunk_size)
                t_listen = time.time()
                buffer, flags, heard = capture_command(stream)
                capture_ms = (time.time() - t_listen) * 1000
                if cancel_event.is_set():
                    emit(type="cancelled", detail="Cancelled while listening")
                elif not heard:
                    print(f"[VAD] No speech heard ({capture_ms:.0f}ms)")
                    emit(type="info", detail="No speech heard")
                else:
                    print(f"[LISTEN] {capture_ms:.0f}ms")
                    transcribe_and_process(buffer, flags, t_listen, capture_ms)
                finish_cycle()
                print("-" * 70)

    except KeyboardInterrupt:
        print("\n\nStopped by user")
    except Exception as e:
        set_state(STATE_ERROR)
        emit(type="error", stage="pipeline", detail=str(e))
        print(f"\n[FATAL] Pipeline crashed: {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        # Clean shutdown: release the audio stream (context exit), the agent session and DB handles
        stream_box["stream"] = None
        broker.cancel_pending(via="shutdown")
        broker.prompter = None
        stop_playback()
        set_state(STATE_STOPPED)
        phase("stopped")
        print("\n[SHUTDOWN] Releasing resources...")
        try:
            agent.unload()
            if hasattr(tts, "close"):
                tts.close()
        except Exception as e:
            print(f"[SHUTDOWN] Agent unload error: {e}")
        try:
            from src.tools.workspace_tools import stop_all_background
            stop_all_background()
        except Exception as e:
            print(f"[SHUTDOWN] Tool cleanup error: {e}")

    print(f"\n{'=' * 70}")
    print(f"  Jarvis stopped. {command_count} commands processed.")
    print(f"  Log: {paths.cache_dir() / 'command_log.jsonl'}")
    print(f"{'=' * 70}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
