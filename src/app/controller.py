"""AssistantController: the one object a front-end talks to.

Owns the pipeline thread and everything shared with it (command queue,
stop / cancel / pause / push-to-talk events, the approval broker, the memory
store) and fans pipeline events out to listeners. Knows nothing about Qt, so
it is testable on its own and the GUI never touches pipeline internals.

Listeners are called from the pipeline thread; GUI listeners must hop to
their UI thread (the Qt layer does this with queued signals).
"""
import queue
import threading
import time
import uuid
from typing import Callable, Optional

from src.core.config_store import load_config
from src.memory.store import ACTIVE_STATUSES, TERMINAL_STATUSES, MemoryStore
from src.tools.approvals import ApprovalBroker, classify_reply

BUSY_PHASES = {"listening", "transcribing", "thinking", "executing", "approval", "speaking"}


class AssistantController:
    def __init__(self, memory: Optional[MemoryStore] = None, pipeline_main: Optional[Callable] = None,
                 pipeline_kwargs: Optional[dict] = None):
        self.memory = memory or MemoryStore()
        self.broker = ApprovalBroker()
        self.commands: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.ptt_event = threading.Event()
        self.listen_event = threading.Event()
        self.interrupt_event = threading.Event()
        self.active_task_id: Optional[int] = None
        self._pipeline_main = pipeline_main
        self._pipeline_kwargs = pipeline_kwargs or {}
        self._thread: Optional[threading.Thread] = None
        self._listeners = []
        self._lock = threading.Lock()
        self.phase = "stopped"
        self.ready_info: dict = {}
        self.voice_ok = False
        self.session_id = uuid.uuid4().hex[:12]

    # ------------------------------------------------------------ listeners

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _emit(self, ev: dict) -> None:
        ev.setdefault("ts", time.time())
        t = ev.get("type")
        if t == "phase":
            self.phase = ev.get("phase", self.phase)
        elif t == "ready":
            self.ready_info = dict(ev)
            self.voice_ok = bool(ev.get("voice"))
        elif t == "task_update":
            task = ev.get("task") or {}
            if task.get("status") in ACTIVE_STATUSES:
                self.active_task_id = task.get("id")
            elif task.get("id") == self.active_task_id:
                self.active_task_id = None
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception as e:
                print(f"[CONTROLLER] Listener error: {e}")

    # ------------------------------------------------------------ lifecycle

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def busy(self) -> bool:
        return self.phase in BUSY_PHASES

    def start(self) -> bool:
        if self.running:
            return False
        self.stop_event.clear()
        self.cancel_event.clear()
        self.ptt_event.clear()
        import src.pipeline as pipeline
        pipeline.stop_flag = False
        while not self.commands.empty():
            try:
                self.commands.get_nowait()
            except queue.Empty:
                break
        self.session_id = uuid.uuid4().hex[:12]
        self.ready_info = {}
        self._emit({"type": "phase", "phase": "loading"})
        self._thread = threading.Thread(target=self._run, name="jarvis-pipeline", daemon=True)
        self._thread.start()
        return True

    def _run(self):
        code = None
        try:
            main = self._pipeline_main
            if main is None:
                from src.pipeline import main
            self._apply_test_audio()
            code = main(
                state_callback=None,
                stop_event=self.stop_event,
                event_callback=self._emit,
                command_queue=self.commands,
                pause_event=self.pause_event,
                cancel_event=self.cancel_event,
                ptt_event=self.ptt_event,
                listen_event=self.listen_event,
                interrupt_event=self.interrupt_event,
                approval_broker=self.broker,
                memory=self.memory,
                session_id=self.session_id,
                **self._pipeline_kwargs,
            )
            if code not in (0, None):
                self._emit({"type": "error", "stage": "pipeline", "detail": f"The assistant stopped (exit code {code}). See the log for details."})
        except Exception as e:
            print(f"[CONTROLLER] Pipeline crashed: {e}")
            self._emit({"type": "error", "stage": "pipeline", "detail": f"Pipeline crashed: {e}"})
        finally:
            self.phase = "stopped"
            self._emit({"type": "phase", "phase": "stopped"})
            self._emit({"type": "stopped", "code": code})

    def _apply_test_audio(self) -> None:
        """Dev/testing only (JARVIS_DEV=1 and JARVIS_TEST_AUDIO=a.wav[;b.wav...]): the microphone is
        replaced by WAV files played in real time, so the orb click -> command path can be
        verified repeatably. Each file is preceded by 6 s of silence."""
        import os
        spec = os.environ.get("JARVIS_TEST_AUDIO")
        if os.environ.get("JARVIS_DEV") != "1" or not spec:
            return
        from src.audio.file_source import FileAudioSource
        script = []
        for path in spec.split(";"):
            script += [(None, 6.0), (path, 0.0)]
        script.append((None, 5.0))
        comps = dict(self._pipeline_kwargs.get("components") or {})
        comps["audio"] = FileAudioSource(script)
        self._pipeline_kwargs["components"] = comps
        print(f"[CONTROLLER] TEST AUDIO instead of the microphone: {spec}")

    def stop(self, wait: float = 15.0) -> bool:
        """Stop the pipeline and wait for it to release the mic, Claude session and audio."""
        if not self.running:
            return True
        if self.active_task_id:
            # Stopping the assistant pauses a running task (resumable) instead of cancelling it.
            try:
                self.memory.update_task(self.active_task_id, control="pause")
            except Exception as e:
                print(f"[CONTROLLER] Could not pause task: {e}")
            self.interrupt_event.set()
            deadline = time.time() + 8
            while self.busy and time.time() < deadline:
                time.sleep(0.1)
        self.stop_event.set()
        self.cancel_event.set()
        self.broker.cancel_pending(via="stop")
        self.ptt_event.clear()
        try:
            import src.pipeline as pipeline
            pipeline.stop_flag = True
        except Exception:
            pass
        self._stop_audio()
        self._thread.join(timeout=wait)
        alive = self._thread.is_alive()
        if alive:
            print(f"[CONTROLLER] Pipeline thread did not stop within {wait:.0f}s")
        return not alive

    def restart(self) -> None:
        self.stop()
        self.start()

    # ------------------------------------------------------------ commands

    def _answer_approval(self, text: str, source: str, via: str) -> bool:
        """A typed yes/no while an approval is pending resolves it instead of becoming a command."""
        pending = self.broker.pending
        if pending is None or not text:
            return False
        cfg = load_config().get("claude_code", {})
        verdict = classify_reply(text, cfg.get("confirm_affirmative", ["yes"]) + ["allow", "y"],
                                 cfg.get("confirm_negative", ["no"]) + ["deny", "n"])
        if verdict not in ("yes", "no"):
            return False
        self._emit({"type": "transcript", "text": text, "source": source})
        self.broker.resolve(pending.id, verdict == "yes", via=via)
        return True

    def send_remote(self, text: str = "", audio_path: str = "", source: str = "phone", ref: str = "") -> str:
        """A command from a remote front-end (text or a voice-note file). The reply events carry `ref`.
        Returns approval | queued | sent | stopped | rejected."""
        text = (text or "").strip()
        if not text and not audio_path:
            return "rejected"
        if self._answer_approval(text, source, via=f"{source} '{text}'"):
            return "approval"
        if not self.running:
            return "stopped"
        queued = self.busy or not self.commands.empty()
        self.commands.put({"type": "remote", "source": source, "ref": ref, "text": text, "audio": audio_path})
        return "queued" if queued else "sent"

    def status(self) -> dict:
        import os
        return {"running": self.running, "phase": self.phase, "busy": self.busy, "muted": self.muted,
                "active_task_id": self.active_task_id, "queued": self.commands.qsize(),
                "pending_approval": self.broker.pending.title if self.broker.pending else None,
                "model": self.ready_info.get("model"), "voice": self.voice_ok,
                "dry_run": bool(self.ready_info.get("dry_run")), "pc": os.environ.get("COMPUTERNAME", "PC")}

    def send_text(self, text: str) -> str:
        """Typed input. Returns what happened: approval | queued | sent | rejected."""
        text = (text or "").strip()
        if not text:
            return "rejected"
        if self._answer_approval(text, "text", via=f"typed '{text}'"):
            return "approval"
        if not self.running:
            self._emit({"type": "error", "stage": "ui", "detail": "The assistant is not running. Press Start."})
            return "rejected"
        queued = self.busy or not self.commands.empty()
        self.commands.put(text)
        if queued:
            self._emit({"type": "info", "detail": f"Queued “{text}” — it runs when the current command finishes."})
            return "queued"
        return "sent"

    def cancel(self) -> bool:
        """Esc / Stop: deny a pending approval, otherwise abort the command in progress."""
        pending = self.broker.pending
        if pending is not None:
            self.broker.resolve(pending.id, False, via="cancelled by user")
            return True
        if not self.busy and not self.ptt_event.is_set():
            return False
        self.ptt_event.clear()
        self.cancel_event.set()
        self._stop_audio()
        self._emit({"type": "info", "detail": "Cancel requested."})
        return True

    # ------------------------------------------------------------ tasks (agent workspace)

    def pause_task(self, task_id: int) -> str:
        task = self.memory.get_task(task_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return "not-active"
        if task_id == self.active_task_id and self.busy:
            self.memory.update_task(task_id, control="pause")
            self.broker.cancel_pending(via="task paused")
            self.interrupt_event.set()            # stop the Claude turn; the orchestrator marks it PAUSED
            return "pausing"
        self.memory.update_task(task_id, status="PAUSED", control=None, blocked_reason="Paused by the user")
        self.memory.add_task_event(task_id, "paused", "Paused by the user")
        self._emit({"type": "task_update", "task": self.memory.get_task(task_id)})
        return "paused"

    def cancel_task(self, task_id: int) -> str:
        task = self.memory.get_task(task_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return "not-active"
        if task_id == self.active_task_id and self.busy:
            self.memory.update_task(task_id, control="cancel")
            self.broker.cancel_pending(via="task cancelled")
            self.cancel_event.set()
            self._stop_audio()
            return "cancelling"
        self.memory.update_task(task_id, status="CANCELLED", control=None, ended_at=time.time())
        self.memory.add_task_event(task_id, "cancelled", "Cancelled by the user")
        self._emit({"type": "task_update", "task": self.memory.get_task(task_id)})
        return "cancelled"

    def resume_task(self, task_id: int, answer: str = "") -> str:
        return self._task_command("resume", task_id, answer)

    def retry_task(self, task_id: int) -> str:
        return self._task_command("retry", task_id)

    def _task_command(self, action: str, task_id: int, answer: str = "") -> str:
        if not self.running:
            self._emit({"type": "error", "stage": "ui", "detail": "The assistant is not running. Press Start."})
            return "rejected"
        queued = self.busy or not self.commands.empty()
        self.commands.put({"type": "task", "action": action, "task_id": int(task_id), "answer": answer})
        if queued:
            self._emit({"type": "info", "detail": f"{action.title()} of task #{task_id} queued."})
        return "queued" if queued else "sent"

    def approve(self, request_id: Optional[int], allowed: bool, via: str = "ui") -> bool:
        return self.broker.resolve(request_id, allowed, via=via)

    def ptt_press(self) -> bool:
        if not self.running or not self.voice_ok or self.busy:
            return False
        self.ptt_event.set()
        return True

    def listen(self) -> bool:
        """Tap to talk (orb click): listen for one spoken command."""
        if not self.running or not self.voice_ok or self.busy:
            return False
        self.listen_event.set()
        return True

    def greet(self) -> bool:
        """The window was opened: speak a new greeting, unless Jarvis is busy or one just played."""
        if not self.running or self.busy or not self.commands.empty():
            return False
        if time.time() - getattr(self, "_greeted_at", 0.0) < 5.0:
            return False
        self._greeted_at = time.time()
        self.commands.put({"type": "greet"})
        return True

    def ptt_release(self) -> None:
        self.ptt_event.clear()

    def set_muted(self, muted: bool) -> None:
        if muted:
            self.pause_event.set()
        else:
            self.pause_event.clear()
        self._emit({"type": "mute", "muted": muted})

    @property
    def muted(self) -> bool:
        return self.pause_event.is_set()

    @staticmethod
    def _stop_audio():
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def shutdown(self) -> None:
        self.stop()
        try:
            self.memory.close()
        except Exception:
            pass
