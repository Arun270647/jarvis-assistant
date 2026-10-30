"""QWebChannel bridge between the HTML UI and the AssistantController.

Every button in the UI calls a slot here, and every slot calls a real backend
function (controller, MemoryStore, config_store, startup). Events from the
pipeline thread reach the page through EventHub's Qt signal, which Qt queues
onto the UI thread.
"""
import json
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from src.core import config_store, logging_setup, paths, startup
from src.gui import native
from src.platform import current

WHISPER_SIZES = ["base", "small", "medium", "large-v3-turbo"]   # downloaded into models/whisper on first use


def _dumps(obj) -> str:
    return json.dumps(obj, default=str)


class EventHub(QObject):
    """Thread-safe fan-out: emit() may be called from any thread."""
    event = Signal(str)
    log = Signal(str)
    quit_requested = Signal()
    quit_later = Signal(int)          # quit after this many ms (from a worker thread)

    def emit_event(self, ev: dict) -> None:
        self.event.emit(_dumps(ev))

    def emit_log(self, level: str, line: str) -> None:
        self.log.emit(_dumps({"level": level, "line": line}))


class Bridge(QObject):
    event = Signal(str)       # pipeline / UI events as JSON
    log = Signal(str)         # diagnostics log lines as JSON

    def __init__(self, app, role: str):
        super().__init__()
        self.app = app            # JarvisQtApp: controller, windows, tray
        self.role = role
        app.hub.event.connect(self.event)
        app.hub.log.connect(self.log)

    @property
    def controller(self):
        return self.app.controller

    # ------------------------------------------------------------ state

    @Slot(result=str)
    def snapshot(self) -> str:
        c = self.controller
        cfg = config_store.load_config()
        pending = c.broker.pending
        history = []
        try:
            history = c.memory.recent_messages(int(cfg.get("memory", {}).get("history_in_ui", 60)))
        except Exception as e:
            history = [{"role": "error", "text": f"Could not load history: {e}", "created_at": time.time()}]
        return _dumps({
            "role": self.role,
            "phase": c.phase,
            "running": c.running,
            "voice_ok": c.voice_ok,
            "muted": c.muted,
            "ready": c.ready_info,
            "pending_approval": {"type": "approval_request", **pending.to_event()} if pending else None,
            "orb_style": cfg.get("gui", {}).get("orb_style", "hud"),
            "assistant_name": config_store.assistant_name(cfg),
            "setup_needed": bool(getattr(self.app, "setup_needed", False)),
            "dry_run": cfg.get("dry_run", False),
            "history": history,
            "log_file": str(logging_setup.LOG_FILE),
            "os": current().name,                       # "windows" or "macos": the page words a few things per OS
            "hotkey": native.HOTKEY_LABEL,
        })

    # ------------------------------------------------------------ assistant

    @Slot(str, result=str)
    def send(self, text: str) -> str:
        return self.controller.send_text(text)

    @Slot(result=bool)
    def cancel(self) -> bool:
        return self.controller.cancel()

    @Slot(int, bool, result=bool)
    def approve(self, request_id: int, allowed: bool) -> bool:
        return self.controller.approve(request_id, allowed, via="button" if self.role == "main" else self.role)

    @Slot(result=bool)
    def pttDown(self) -> bool:
        return self.controller.ptt_press()

    @Slot(result=bool)
    def listen(self) -> bool:
        return self.controller.listen()

    @Slot()
    def pttUp(self) -> None:
        self.controller.ptt_release()

    @Slot(result=bool)
    def toggleMute(self) -> bool:
        self.controller.set_muted(not self.controller.muted)
        self.app.refresh_tray()
        return self.controller.muted

    @Slot()
    def start(self) -> None:
        self.controller.start()

    @Slot()
    def stop(self) -> None:
        threading.Thread(target=self.controller.stop, name="jarvis-stop", daemon=True).start()

    @Slot()
    def restart(self) -> None:
        threading.Thread(target=self.controller.restart, name="jarvis-restart", daemon=True).start()

    # ------------------------------------------------------------ windows

    @Slot()
    def collapse(self) -> None:
        self.app.collapse_to_orb()

    @Slot()
    def expand(self) -> None:
        self.app.show_main()

    @Slot()
    def toggleMax(self) -> None:
        self.app.toggle_maximized()

    @Slot()
    def startMove(self) -> None:
        self.app.start_compact_move()

    @Slot()
    def quit(self) -> None:
        self.app.quit()

    # ------------------------------------------------------------ memory

    @Slot(str, result=str)
    def memoryList(self, query: str) -> str:
        m = self.controller.memory
        return _dumps({"items": m.list_memories(query or ""), "count": m.count_memories()})

    @Slot(str, str, bool, result=str)
    def memoryAdd(self, content: str, kind: str, pinned: bool) -> str:
        try:
            return _dumps({"ok": True, "item": self.controller.memory.add_memory(content, kind, source="user", pinned=pinned)})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

    @Slot(int, str, str, bool, result=str)
    def memoryUpdate(self, memory_id: int, content: str, kind: str, pinned: bool) -> str:
        try:
            row = self.controller.memory.update_memory(memory_id, content, kind, pinned)
            return _dumps({"ok": row is not None, "item": row, "error": None if row else "Not found"})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

    @Slot(int, result=bool)
    def memoryDelete(self, memory_id: int) -> bool:
        return self.controller.memory.delete_memory(memory_id)

    @Slot(str, result=str)
    def historyList(self, query: str) -> str:
        m = self.controller.memory
        rows = m.search_messages(query, limit=200) if query.strip() else list(reversed(m.recent_messages(200)))
        return _dumps(rows)

    @Slot(result=int)
    def historyClear(self) -> int:
        return self.controller.memory.clear_messages()

    # ------------------------------------------------------------ agent workspace

    @Slot(int, result=str)
    def taskList(self, limit: int) -> str:
        return _dumps({"tasks": self.controller.memory.list_tasks(limit=limit or 50),
                       "active": self.controller.active_task_id})

    @Slot(int, result=str)
    def taskGet(self, task_id: int) -> str:
        m = self.controller.memory
        task = m.get_task(task_id)
        if task is None:
            return _dumps(None)
        task["events"] = m.task_events(task_id)
        task["runs"] = m.task_tool_runs(task_id, 100)
        return _dumps(task)

    @Slot(int, result=str)
    def taskPause(self, task_id: int) -> str:
        return self.controller.pause_task(task_id)

    @Slot(int, result=str)
    def taskCancel(self, task_id: int) -> str:
        return self.controller.cancel_task(task_id)

    @Slot(int, str, result=str)
    def taskResume(self, task_id: int, answer: str) -> str:
        return self.controller.resume_task(task_id, answer)

    @Slot(int, result=str)
    def taskRetry(self, task_id: int) -> str:
        return self.controller.retry_task(task_id)

    @Slot(result=str)
    def activityList(self) -> str:
        return _dumps(self.controller.memory.recent_tool_runs(150))

    # ------------------------------------------------------------ settings

    @Slot(result=str)
    def settingsGet(self) -> str:
        cfg = config_store.load_config()
        return _dumps({"config": cfg, "options": self._options(cfg), "startup_enabled": startup.is_startup_enabled(),
                       "editable": list(config_store.EDITABLE), "ai": self._ai_status(cfg)})

    # ------------------------------------------------------------ AI providers

    def _ai_status(self, cfg: dict) -> dict:
        """Every provider with what Settings › AI shows about it; keys only as where they come from."""
        from src.agent import providers as P
        from src.agent.providers.openai_compat import guess_vision
        from src.agent.utility import describe
        items = []
        for name in P.providers(cfg):
            try:
                s = P.settings_for(cfg, name)
            except Exception:
                continue
            source = P.api_key(name, s)[1] if s["kind"] != "claude_code" else None
            vision = s.get("vision")
            if s["kind"] != "openai_compat":
                vision = True
            items.append({"name": name, "label": s.get("label") or name, "kind": s["kind"],
                          "needs_key": bool(s.get("needs_key")), "local": bool(s.get("local")),
                          "key_source": source, "env_key": s.get("env_key") or "", "models": s.get("models") or [],
                          "vision": vision, "vision_guess": guess_vision(str(s.get("model") or ""))})
        return {"providers": items, "problems": P.problems(cfg, "main"), "utility": describe(cfg)}

    @Slot(str, str, result=str)
    def aiSaveKey(self, provider: str, key: str) -> str:
        from src.agent import providers as P
        try:
            P.settings_for(config_store.load_config(), provider)
            P.save_api_key(provider, key)
            return _dumps({"ok": True})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

    @Slot(str, result=bool)
    def aiForgetKey(self, provider: str) -> bool:
        from src.agent import providers as P
        P.forget_api_key(provider)
        return True

    @Slot(str, str)
    def aiFetchModels(self, provider: str, base_url: str) -> None:
        """The provider's model list (GET /models), answered with an ai_models event."""
        def run():
            from src.agent import providers as P
            cfg = config_store.load_config()
            try:
                if base_url:
                    cfg["llm"]["providers"][provider]["base_url"] = base_url
                adapter = P.adapter_for(cfg, provider)
                try:
                    models = adapter.list_models()
                finally:
                    adapter.close()
                self.app.hub.emit_event({"type": "ai_models", "provider": provider, "ok": True, "models": models})
            except Exception as e:
                self.app.hub.emit_event({"type": "ai_models", "provider": provider, "ok": False, "error": str(e)})
        threading.Thread(target=run, name="ai-models", daemon=True).start()

    @Slot(result=str)
    def usageSummary(self) -> str:
        from src.agent.usage import UsageMeter
        cfg = config_store.load_config()
        meter = UsageMeter(self.controller.memory, lambda: cfg)
        try:
            days = self.controller.memory.usage_by_day(14)
        except Exception as e:
            return _dumps({"days": [], "error": str(e)})
        return _dumps({"days": days, "today_usd": meter.spent_today(), "budget_usd": meter.budget()})

    @Slot(str, result=str)
    def checkAssistantName(self, name: str) -> str:
        error, warning = config_store.check_assistant_name(name)
        return _dumps({"error": error, "warning": warning})

    @Slot(str, result=str)
    def settingsSave(self, updates_json: str) -> str:
        try:
            updates = json.loads(updates_json)
            startup_wanted = updates.pop("__startup__", None)
            if updates:
                config_store.update_config(updates)
            if startup_wanted is not None:
                startup.set_startup(bool(startup_wanted))
            restart_needed = any(not k.startswith(("gui.", "tools.permissions", "tools.approval", "tools.untrusted_asks",
                                                   "voice_approval.high_risk"))
                                 for k in updates)
            self.app.apply_gui_settings()
            return _dumps({"ok": True, "restart_needed": restart_needed and self.controller.running})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

    # ------------------------------------------------------------ setup wizard (src/app/setup.py)

    def _downloads(self):
        if getattr(self.app, "downloads", None) is None:
            from src.core.downloads import Downloader
            self.app.downloads = Downloader(emit=self.app.hub.emit_event)
        return self.app.downloads

    def _facts(self) -> dict:
        if getattr(self.app, "hardware_facts", None) is None:
            from src.core.hardware import detect
            self.app.hardware_facts = detect()
        return self.app.hardware_facts

    @Slot(result=str)
    def setupState(self) -> str:
        """Everything the wizard shows: hardware-based model picks, their download state, the AI providers."""
        from src.app import setup
        cfg = config_store.load_config()
        state = setup.state(cfg, self._facts())
        state["downloads"] = self._downloads().jobs()
        state["ai"] = self._ai_status(cfg)
        state["devices"] = self._options(cfg).get("devices", [])
        return _dumps(state)

    @Slot(str, str, result=str)
    def setupSave(self, step: str, data_json: str) -> str:
        from src.app import setup
        try:
            data = json.loads(data_json or "{}")
        except ValueError:
            return _dumps({"ok": False, "error": "Bad data"})
        result = setup.save_step(step, data, self._facts())
        if result.get("ok"):
            for option_id in result.get("downloads", []):
                self._downloads().start(option_id)
            if step == "name":
                self.app.apply_gui_settings()
            if step == "finish":
                self.app.setup_needed = False
        return _dumps(result)

    @Slot(str)
    def setupDownload(self, option_id: str) -> None:
        try:
            self._downloads().start(option_id)
        except KeyError as e:
            self.app.hub.emit_event({"type": "download", "option": option_id, "state": "failed", "error": str(e)})

    @Slot(str)
    def setupCancelDownload(self, option_id: str) -> None:
        self._downloads().cancel(option_id)

    @Slot(str, str, result=str)
    def setupImportFile(self, option_id: str, file_name: str) -> str:
        """"I already have this file": pick it, check it against the catalogue, copy it in."""
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self.app.main, f"Choose {file_name}", str(Path.home() / "Downloads"),
                                              f"{file_name} (*{Path(file_name).suffix});;All files (*)")
        if not path:
            return _dumps({"ok": False, "cancelled": True})
        return _dumps(self._downloads().import_file(option_id, file_name, path))

    @Slot(str)
    def openUrl(self, url: str) -> None:
        """Official pages, plans and manual downloads open in the user's browser (web addresses only)."""
        if url.startswith(("https://", "http://")):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))

    @Slot(result=str)
    def pickFolder(self) -> str:
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getExistingDirectory(self.app.main, "Choose a folder", str(Path.home())) or ""

    @Slot(str)
    def openHelp(self, anchor: str) -> None:
        """A ? button: the matching section of the user guide (the website's copy once help.url is set, until then
        the guide that ships in docs/)."""
        url = (config_store.load_config().get("help", {}) or {}).get("url", "")
        if url.startswith(("https://", "http://")):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url + (f"#{anchor}" if anchor else "")))
            return
        guide = paths.APP_ROOT / "docs" / "GETTING_STARTED.md"
        if guide.exists():
            current().open_path(str(guide))

    @Slot()
    def openMicPrivacy(self) -> None:
        current().privacy_settings("microphone")

    # ------------------------------------------------------------ permissions (macOS asks for each)

    @Slot(result=str)
    def permissions(self) -> str:
        """{permission: 'granted' | 'denied' | 'not_determined' | 'unknown'} (src.platform.PERMISSIONS)."""
        from src.platform import PERMISSIONS
        return _dumps({kind: current().permission(kind) for kind in PERMISSIONS})

    @Slot(str, result=str)
    def requestPermission(self, kind: str) -> str:
        """Ask macOS for a permission (its own prompt, shown once); the page offers System Settings after that."""
        return current().request_permission(kind)

    @Slot(str, result=bool)
    def openPrivacySettings(self, kind: str) -> bool:
        return current().privacy_settings(kind)

    @Slot(str, str)
    def hearVoice(self, option_id: str, name: str) -> None:
        """▶ on a voice in the wizard: a downloaded voice says hello itself. One that isn't downloaded yet sends
        the page its online sample to play (an empty url when it has none)."""
        def run():
            try:
                from src.core import models
                from src.tts import preview_engine
                _, option = models.find(option_id)
                if option.get("needs_key") == "fish":
                    from src.tts import fish
                    if not fish.api_key()[0]:
                        self.app.hub.emit_event({"type": "voice_sample", "option": option_id, "url": "", "needs_key": True})
                        return
                if models.status(option)["state"] not in ("installed", "builtin"):
                    self.app.hub.emit_event({"type": "voice_sample", "option": option_id, "url": option.get("sample") or ""})
                    return
                preview_engine(option.get("config", {}), config_store.load_config()).speak(f"Hi, I'm {name}.")
            except Exception as e:
                print(f"[SETUP] Could not play {option_id}: {e}")
        threading.Thread(target=run, name="hear-voice", daemon=True).start()

    @Slot(result=str)
    def fishKeyStatus(self) -> str:
        """Where the Fish Audio API key comes from: "saved", "env:FISH_API_KEY" or null."""
        from src.tts import fish
        return _dumps({"source": fish.api_key()[1]})

    @Slot(str, result=str)
    def fishSaveKey(self, key: str) -> str:
        from src.tts import fish
        try:
            fish.save_api_key(key)
            return _dumps({"ok": True})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

    @Slot(result=bool)
    def fishForgetKey(self) -> bool:
        from src.tts import fish
        fish.forget_api_key()
        return True

    @Slot(str)
    def hearName(self, name: str) -> None:
        """"Hear it": the name spoken by the system voice (the chosen voice may still be downloading)."""
        def run():
            try:
                from src.tts.system import SystemTTS
                SystemTTS().speak(f"Hi, I'm {name}.")
            except Exception as e:
                print(f"[SETUP] Could not speak the name: {e}")
        threading.Thread(target=run, name="hear-name", daemon=True).start()

    @Slot()
    def claudeStatus(self) -> None:
        def run():
            from src.app import setup
            self.app.hub.emit_event(dict(setup.claude_status(), type="claude_status"))
        threading.Thread(target=run, name="claude-status", daemon=True).start()

    @Slot()
    def claudeLogin(self) -> None:
        """Sign in with Claude: the browser opens; the status is checked every 2 s for up to 5 minutes."""
        def run():
            from src.app import setup
            emit = self.app.hub.emit_event
            self._login_cancel = threading.Event()
            try:
                proc = setup.start_claude_login()
            except Exception as e:
                emit({"type": "claude_login", "state": "error", "error": str(e)})
                return
            emit({"type": "claude_login", "state": "waiting"})
            deadline = time.time() + 300
            try:
                while time.time() < deadline and not self._login_cancel.wait(2):
                    status = setup.claude_status()
                    if status.get("signed_in"):
                        config_store.update_config({"llm.main.provider": "claude_code"})
                        emit(dict(status, type="claude_login", state="signed_in"))
                        return
                emit({"type": "claude_login", "state": "cancelled" if self._login_cancel.is_set() else "timeout"})
            finally:
                if proc.poll() is None:
                    proc.kill()
        threading.Thread(target=run, name="claude-login", daemon=True).start()

    @Slot()
    def claudeInstall(self) -> None:
        def run():
            from src.app import setup
            self.app.hub.emit_event({"type": "claude_install", "state": "running"})
            result = setup.install_claude_code()
            self.app.hub.emit_event(dict(result, type="claude_install", state="done" if result["ok"] else "failed"))
        threading.Thread(target=run, name="claude-install", daemon=True).start()

    @Slot()
    def claudeLoginCancel(self) -> None:
        if getattr(self, "_login_cancel", None) is not None:
            self._login_cancel.set()

    @Slot(str)
    def micTest(self, device: str) -> None:
        """Record up to 5 seconds, report the level live (mic_level) and what was heard (mic_test)."""
        def run():
            import numpy as np
            emit = self.app.hub.emit_event
            index = None if device in ("", "null", "None") else int(device)
            try:
                import sounddevice as sd
                rate, chunk, frames, voiced = 16000, 800, [], 0
                with sd.InputStream(device=index, channels=1, samplerate=rate, dtype="int16", blocksize=chunk) as stream:
                    for _ in range(int(5 * rate / chunk)):
                        data, _ = stream.read(chunk)
                        frames.append(data.copy())
                        rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                        level = min(1.0, rms / 4000)
                        voiced += 1 if rms > 600 else 0
                        emit({"type": "mic_level", "v": round(level, 3)})
            except Exception as e:
                text = str(e)
                blocked = any(s in text.lower() for s in ("unanticipated host error", "access", "-9999"))
                emit({"type": "mic_test", "ok": False, "blocked": blocked, "error": text})
                return
            audio = np.concatenate(frames).reshape(-1)
            if voiced * chunk / rate < 0.3:
                emit({"type": "mic_test", "ok": False, "error": "I didn't hear anything. Is the right microphone selected?"})
                return
            heard = ""
            try:
                from src.core import paths as p
                if Path(p.whisper_model(config_store.load_config()["stt"]["model_path"])[0]).exists():
                    from src.stt.transcriber import transcribe_audio
                    heard = transcribe_audio(audio)
            except Exception as e:
                print(f"[SETUP] Mic test transcription skipped: {e}")
            emit({"type": "mic_test", "ok": True, "heard": heard})
        threading.Thread(target=run, name="mic-test", daemon=True).start()

    @Slot(result=str)
    def modelsState(self) -> str:
        """Settings › Models: the catalogue with what is in use, downloaded or downloading."""
        from src.app import setup
        cfg = config_store.load_config()
        return _dumps({"groups": setup.catalogue_view(config_store.assistant_name(cfg), self._facts(), cfg),
                       "jobs": self._downloads().jobs(), "models_dir": str(paths.models_dir()), "facts": self._facts()})

    @Slot(str, result=str)
    def modelsUse(self, option_id: str) -> str:
        from src.app import setup
        try:
            result = setup.use_model(option_id, self._facts())
            if result["download"]:
                self._downloads().start(option_id)
            return _dumps(result)
        except (KeyError, ValueError) as e:
            return _dumps({"ok": False, "error": str(e)})

    @Slot(str, result=str)
    def modelsDelete(self, option_id: str) -> str:
        from src.core import models
        try:
            _, option = models.find(option_id)
            self._downloads().cancel(option_id)
            return _dumps({"ok": True, "freed": models.delete(option)})
        except (KeyError, OSError) as e:
            return _dumps({"ok": False, "error": str(e)})

    # ------------------------------------------------------------ about, updates, uninstall

    @Slot(result=str)
    def aboutInfo(self) -> str:
        from src.app import install, uninstall, updates
        from src.version import __version__
        cfg = config_store.load_config()
        return _dumps({"version": __version__, "data": str(paths.home()), "models": str(paths.models_dir()),
                       "installed": uninstall.installed_copy(), "updates_url": bool(cfg.get("updates", {}).get("url")),
                       "updater": updates.updater_available(), "update_ready": updates.staged_version(),
                       "install": install.state() if sys.platform in ("win32", "darwin") else {"available": False}})

    @Slot()
    def checkUpdates(self) -> None:
        def run():
            from src.app import updates
            result = updates.check(config_store.load_config().get("updates", {}).get("url", ""))
            self.app.hub.emit_event(dict(result, type="update_check", can_install=updates.updater_available()))
        threading.Thread(target=run, name="update-check", daemon=True).start()

    @Slot()
    def updateInstall(self) -> None:
        """Settings › About › Download and install: download, check and unpack the new version in the background,
        then offer Restart to update."""
        threading.Thread(target=self.app.prepare_update, name="update-download", daemon=True).start()

    @Slot(result=str)
    def updateRestart(self) -> str:
        """Restart to update: the updater swaps in the new version after Jarvis quits, then starts it again."""
        from src.app import updates
        try:
            updates.launch_updater()
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, self.app.quit)
        return _dumps({"ok": True})

    @Slot(bool, bool)
    def installForMe(self, desktop: bool, remove_copy: bool) -> None:
        """Install for me: copy to %LOCALAPPDATA%\\Programs\\Jarvis (or Jarvis.app to Applications) in the background
        (about a gigabyte), then quit so the installed copy can start."""
        def run():
            from src.app import install
            self.app.hub.emit_event({"type": "install", "state": "copying", "to": install.state()["to"]})
            try:
                result = install.install(desktop=desktop, remove_copy=remove_copy)
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            self.app.hub.emit_event(dict(result, type="install", state="done" if result.get("ok") else "failed"))
            if result.get("ok"):
                self.app.hub.quit_later.emit(1500)
        threading.Thread(target=run, name="install", daemon=True).start()

    @Slot(bool, result=str)
    def uninstallPlan(self, delete_data: bool) -> str:
        from src.app import uninstall
        return _dumps(uninstall.plan(delete_data))

    @Slot(bool, result=str)
    def uninstallRun(self, delete_data: bool) -> str:
        """Settings › About › Uninstall: remove, then quit (folders go once Jarvis has exited)."""
        from src.app import uninstall
        try:
            result = uninstall.run(uninstall.plan(delete_data))
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, self.app.quit)
        return _dumps(result)

    @Slot()
    def setupStartAssistant(self) -> None:
        if not self.controller.running:
            self.controller.start()

    @Slot()
    def setupRunAgain(self) -> None:
        """Settings › Run setup again."""
        config_store.update_config({"setup.complete": False, "setup.step": ""})
        self.app.setup_needed = True
        self.app.main.view.reload()

    # ------------------------------------------------------------ email account

    @Slot(result=str)
    def emailStatus(self) -> str:
        from src.tools.email_tools import has_password, smtp_settings
        e = smtp_settings(config_store.load_config())
        user = e.get("username") or e.get("from_address") or ""
        return _dumps({"username": user, "password_saved": has_password(user), "host": e.get("smtp_host")})

    @Slot(str, result=str)
    def emailSavePassword(self, password: str) -> str:
        from src.tools.email_tools import set_password, smtp_settings
        e = smtp_settings(config_store.load_config())
        user = e.get("username") or e.get("from_address")
        if not user:
            return _dumps({"ok": False, "error": "Set the username first and save settings."})
        if not password:
            return _dumps({"ok": False, "error": "Empty password."})
        try:
            set_password(user, password)
            return _dumps({"ok": True})
        except Exception as ex:
            return _dumps({"ok": False, "error": str(ex)})

    @Slot(result=bool)
    def emailForgetPassword(self) -> bool:
        from src.tools.email_tools import delete_password, smtp_settings
        e = smtp_settings(config_store.load_config())
        delete_password(e.get("username") or e.get("from_address") or "")
        return True

    @Slot()
    def emailTest(self) -> None:
        def run():
            from src.tools.email_tools import send_mail, smtp_settings
            cfg = config_store.load_config()
            to = smtp_settings(cfg).get("from_address") or smtp_settings(cfg).get("username")
            try:
                mid = send_mail(cfg, [to], "Jarvis test email", "This is a test email sent by Jarvis from Settings.")
                self.app.hub.emit_event({"type": "email_test", "ok": True, "detail": f"Sent to {to} ({mid})"})
            except Exception as ex:
                self.app.hub.emit_event({"type": "email_test", "ok": False, "detail": str(ex)})
        threading.Thread(target=run, name="email-test", daemon=True).start()

    @Slot()
    def testConnection(self) -> None:
        threading.Thread(target=self._test_connection, name="ai-test", daemon=True).start()

    def _test_connection(self):
        """One request through the configured provider that has to use a tool (the clock), on saved settings."""
        import tempfile
        t0 = time.time()
        try:
            from src.agent import create_agent
            from src.memory.store import MemoryStore
            from src.router.action_router import ActionRouter
            from src.tools.policy import PolicyExecutor
            from src.tools.registry import ToolContext, build_default_registry

            cfg = config_store.load_config()
            cfg["dry_run"] = True
            router = ActionRouter()
            router.dry_run = True
            mem = MemoryStore(Path(tempfile.mkdtemp(prefix="jarvis-test-")) / "t.db")
            executor = PolicyExecutor(build_default_registry(router, mem, cfg), lambda: cfg, None, mem)
            agent = create_agent(cfg, executor, mem)
            agent.load()
            try:
                res = agent.handle("What time is it? Use your clock tool.", "text", ToolContext(session_id="test"), lambda e: None)
            finally:
                agent.unload()
            ok = res.ok and any(c.get("tool") == "get_datetime" for c in res.tool_calls)
            detail = (f"{agent.name} · {agent.model}: “{res.reply}”" if res.ok else res.error)
            if res.ok and not ok:
                detail += " (answered without calling the clock tool)"
            self.app.hub.emit_event({"type": "settings_test", "ok": ok, "detail": detail, "seconds": round(time.time() - t0, 1)})
        except Exception as e:
            self.app.hub.emit_event({"type": "settings_test", "ok": False, "detail": str(e), "seconds": round(time.time() - t0, 1)})

    def _options(self, cfg: dict) -> dict:
        opts = {"devices": [], "voices": [], "whisper_models": [], "tools": [], "prompt_tokens": {}}
        try:
            import sounddevice as sd
            default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    host = sd.query_hostapis(d["hostapi"])["name"]
                    opts["devices"].append({"index": i, "name": f"{d['name']} ({host})", "default": i == default_in})
        except Exception as e:
            opts["devices_error"] = str(e)
        piper_dir = paths.model_path(cfg.get("tts", {}).get("piper", {}).get("model_dir") or "piper")
        opts["voices"] = sorted(p.name[:-5] for p in piper_dir.glob("*.onnx"))
        from src.tts import kokoro
        opts["kokoro_voices"] = kokoro.voices(cfg)
        from src.tts.system import system_voices
        opts["system_voices"] = system_voices()
        # Model names, plus local CTranslate2 model folders the user put in models/whisper.
        local = paths.models_dir() / "whisper"
        opts["whisper_models"] = WHISPER_SIZES + sorted(p.name for p in local.glob("*") if (p / "model.bin").exists())
        current = cfg.get("stt", {}).get("model_path")
        if current and current not in opts["whisper_models"]:
            opts["whisper_models"].append(current)
        try:
            from src.router.action_router import ActionRouter
            from src.tools import groups
            from src.tools.extensions import available_extensions
            from src.tools.registry import ToolRegistry, build_default_registry
            reg = build_default_registry(ActionRouter(), self.controller.memory, cfg, apply_groups=False)
            shown = ToolRegistry()                   # what the model sees: tools in groups that are on
            for s in reg.specs():
                if groups.tool_enabled(s.name, cfg):
                    shown.register(s)
            opts["prompt_tokens"] = self._prompt_tokens(shown)
            overrides = cfg.get("tools", {}).get("permissions", {})
            on = groups.enabled_groups(cfg)
            opts["tool_groups"] = [{"id": gid, "title": g["title"], "text": g["text"], "locked": bool(g.get("locked")),
                                    "on": gid in on} for gid, g in groups.GROUPS.items()]
            for s in reg.specs():
                gid = groups.group_of(s.name)
                opts["tools"].append({"name": s.name, "description": s.description.split(". ")[0].rstrip(".") + ".",
                                      "risk": s.risk, "default": s.permission,
                                      "per_arg": s.needs_confirmation is not None,
                                      "always": s.always_ask is True, "note": groups.ALWAYS_ASK_NOTES.get(s.name, ""),
                                      "group": gid or "", "group_title": groups.GROUPS[gid]["title"] if gid else "Extension",
                                      "permission": overrides.get(s.name, s.permission)})
            opts["extensions"] = available_extensions()
        except Exception as e:
            opts["tools_error"] = str(e)
        return opts

    @staticmethod
    def _prompt_tokens(reg) -> dict:
        """About how many tokens the system prompt and tool definitions take, per tool profile (for the cost
        estimate in Settings › AI). Planning tools are added by the pipeline, so they count as ~1500."""
        from src.agent.prompt import build_agent_system_prompt
        from src.agent.toolsets import CORE_TOOLS
        out = {}
        for profile in ("full", "core"):
            specs = [s for s in reg.specs() if profile == "full" or s.name in CORE_TOOLS]
            chars = len(build_agent_system_prompt([s.name for s in specs], {}, False))
            chars += sum(len(s.description) + len(json.dumps(s.input_schema)) for s in specs)
            out[profile] = chars // 4 + 1500
        return out

    # ------------------------------------------------------------ diagnostics

    @Slot(result=str)
    def saveReport(self) -> str:
        """Settings › Save a report: the redacted diagnostic report, shown in Explorer or the Finder."""
        try:
            from src.core.diagnose import save_report
            path = save_report()
            current().reveal(str(path))
            return _dumps({"ok": True, "path": str(path)})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

    @Slot()
    def openLog(self) -> None:
        current().open_path(str(logging_setup.LOG_FILE))

    @Slot()
    def openLogFolder(self) -> None:
        current().open_path(str(logging_setup.LOG_DIR))

    @Slot()
    def openConfigFolder(self) -> None:
        """The user's data folder: config.json (their settings), user_profile.json, the database and logs."""
        home = paths.home()
        home.mkdir(parents=True, exist_ok=True)
        current().open_path(str(home))

    @Slot(int, result=str)
    def logTail(self, n: int) -> str:
        return _dumps([l.rstrip("\n") for l in logging_setup.tail(n)])
