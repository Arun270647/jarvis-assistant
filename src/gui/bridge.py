"""QWebChannel bridge between the HTML UI and the AssistantController.

Every button in the UI calls a slot here, and every slot calls a real backend
function (controller, MemoryStore, config_store, startup). Events from the
pipeline thread reach the page through EventHub's Qt signal, which Qt queues
onto the UI thread.
"""
import json
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from src.core import config_store, logging_setup, paths, startup

WHISPER_SIZES = ["base", "small", "medium", "large-v3-turbo"]   # downloaded into models/whisper on first use


def _dumps(obj) -> str:
    return json.dumps(obj, default=str)


class EventHub(QObject):
    """Thread-safe fan-out: emit() may be called from any thread."""
    event = Signal(str)
    log = Signal(str)
    quit_requested = Signal()

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
            "dry_run": cfg.get("dry_run", False),
            "history": history,
            "log_file": str(logging_setup.LOG_FILE),
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
    def settingsSave(self, updates_json: str) -> str:
        try:
            updates = json.loads(updates_json)
            startup_wanted = updates.pop("__startup__", None)
            if updates:
                config_store.update_config(updates)
            if startup_wanted is not None:
                startup.set_startup(bool(startup_wanted))
            restart_needed = any(not k.startswith(("gui.", "tools.permissions", "tools.approval", "phone."))
                                 for k in updates)
            if any(k in ("phone.enabled", "phone.port", "phone.bind") for k in updates):
                self._restart_phone()
            self.app.apply_gui_settings()
            return _dumps({"ok": True, "restart_needed": restart_needed and self.controller.running})
        except Exception as e:
            return _dumps({"ok": False, "error": str(e)})

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

    # ------------------------------------------------------------ phone app

    def _restart_phone(self) -> None:
        threading.Thread(target=self.app.restart_phone_server, name="phone-restart", daemon=True).start()

    @Slot(result=str)
    def phoneStatus(self) -> str:
        server = self.app.phone
        enabled = config_store.load_config().get("phone", {}).get("enabled", False)
        if server is not None:
            return _dumps(dict(server.status(), enabled=enabled))
        devices = []
        try:
            devices = self.controller.memory.list_phone_devices()
        except Exception:
            pass
        return _dumps({"enabled": enabled, "running": False, "error": self.app.phone_error, "devices": devices,
                       "connected": 0, "pair_code": None})

    @Slot(result=str)
    def phonePairStart(self) -> str:
        server = self.app.phone
        if server is None or not server.running:
            return _dumps({"ok": False, "error": self.app.phone_error or "Turn on the phone app server first."})
        return _dumps(dict(server.new_pair_code(), ok=True))

    @Slot(str, result=bool)
    def phoneRevoke(self, device_id: str) -> bool:
        if self.app.phone is not None:
            return self.app.phone.revoke(device_id)
        return self.controller.memory.delete_phone_device(device_id)

    @Slot()
    def phoneRestart(self) -> None:
        self._restart_phone()

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
        # Model names, plus local CTranslate2 model folders the user put in models/whisper.
        local = paths.models_dir() / "whisper"
        opts["whisper_models"] = WHISPER_SIZES + sorted(p.name for p in local.glob("*") if (p / "model.bin").exists())
        current = cfg.get("stt", {}).get("model_path")
        if current and current not in opts["whisper_models"]:
            opts["whisper_models"].append(current)
        try:
            from src.router.action_router import ActionRouter
            from src.tools.registry import build_default_registry
            reg = build_default_registry(ActionRouter(), self.controller.memory, cfg)
            opts["prompt_tokens"] = self._prompt_tokens(reg)
            overrides = cfg.get("tools", {}).get("permissions", {})
            for s in reg.specs():
                opts["tools"].append({"name": s.name, "description": s.description.split(". ")[0].rstrip(".") + ".",
                                      "risk": s.risk, "default": s.permission,
                                      "per_arg": s.needs_confirmation is not None,
                                      "permission": overrides.get(s.name, s.permission)})
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

    @Slot()
    def openLog(self) -> None:
        os.startfile(str(logging_setup.LOG_FILE))

    @Slot()
    def openLogFolder(self) -> None:
        os.startfile(str(logging_setup.LOG_DIR))

    @Slot()
    def openConfigFolder(self) -> None:
        """The user's data folder: config.json (their settings), user_profile.json, the database and logs."""
        home = paths.home()
        home.mkdir(parents=True, exist_ok=True)
        os.startfile(str(home))

    @Slot(int, result=str)
    def logTail(self, n: int) -> str:
        return _dumps([l.rstrip("\n") for l in logging_setup.tail(n)])
