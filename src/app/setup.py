"""The first-run setup wizard's backend (the screens are src/gui/web/setup.js).

Shown while setup.complete is false: on a new install, or after "Run setup again". Each screen saves
through save_step(), which writes ordinary settings (and the profile for "About you") and remembers the step,
so a wizard closed halfway resumes where it stopped. Nothing here needs Qt, so it is tested directly.

Screens: name → models → connect → microphone → voice → about → permissions → startup → try.
"""
import json
import re
import shutil
from pathlib import Path
from typing import Optional

from src.core import config_store, hardware, models, paths, winproc

STEPS = ("name", "models", "connect", "microphone", "voice", "about", "permissions", "startup", "try")
NAME_IDEAS = ("Jarvis", "Nova", "Atlas", "Echo", "Iris")

# What may {name} do: a preset denies the tool groups it doesn't include (permissions still apply to the rest).
DESKTOP_TOOLS = ("desktop_snapshot", "desktop_screenshot", "desktop_click", "desktop_type", "desktop_keys",
                 "desktop_scroll", "desktop_focus")
WORK_TOOLS = ("edit_file", "write_file", "delete_file", "run_command", "process_output", "stop_process",
              "open_terminal", "terminal_type", "terminal_key", "terminal_read", "check_process", "http_check",
              "send_email", "delegate_to_coding_agent")
PRESETS = {
    "everyday": {"title": "Everyday",
                 "text": "Open apps, control music and volume, tell the time, read and search the web, remember "
                         "things you tell it.",
                 "deny": DESKTOP_TOOLS + WORK_TOOLS},
    "work": {"title": "Work",
             "text": "Everyday, plus read and edit files in your folders, run project commands, open terminals and "
                     "send email. Always asks before changes.",
             "deny": DESKTOP_TOOLS},
    "full": {"title": "Full control",
             "text": "Work, plus click and type in other apps for you and take screenshots.",
             "deny": ()},
}


def needed(cfg: Optional[dict] = None) -> bool:
    cfg = config_store.load_config() if cfg is None else cfg
    return not (cfg.get("setup", {}) or {}).get("complete", False)


def current_preset(cfg: dict) -> str:
    preset = (cfg.get("tools", {}) or {}).get("preset", "")
    return preset if preset in PRESETS else "everyday"


def catalogue_view(name: str, facts: dict, cfg: dict) -> list:
    """The model groups for "What {name} needs": every option with its status, the recommendation and what is
    selected now (from the settings)."""
    rec = models.recommend(facts)
    chosen = _chosen_options(cfg)
    out = []
    for g in models.groups():
        options = []
        for o in g["options"]:
            st = models.status(o)
            options.append({**{k: o.get(k) for k in ("id", "name", "summary", "about", "license", "license_url", "page",
                                                    "size_mb", "sample")},
                            "summary": models.text(o.get("summary"), name), "about": models.text(o.get("about"), name),
                            "files": [{"name": f["name"], "url": f["url"], "size": f["size"]} for f in o.get("files", [])],
                            "status": st["state"], "have": st["have"], "total": st["total"],
                            "recommended": rec.get(g["id"], {}).get("option") == o["id"]})
        out.append({"id": g["id"], "icon": g.get("icon"), "title": models.text(g["title"], name),
                    "explain": models.text(g["explain"], name), "options": options,
                    "recommended": rec.get(g["id"], {}).get("option"), "reason": rec.get(g["id"], {}).get("reason", ""),
                    "selected": chosen.get(g["id"]) or rec.get(g["id"], {}).get("option")})
    return out


def _chosen_options(cfg: dict) -> dict:
    """Which catalogue option the current settings correspond to, per group (only once the user chose)."""
    picks = (cfg.get("setup", {}) or {}).get("models", {}) or {}
    return {g: o for g, o in picks.items() if isinstance(o, str)}


def state(cfg: Optional[dict] = None, facts: Optional[dict] = None) -> dict:
    cfg = config_store.load_config() if cfg is None else cfg
    facts = hardware.detect() if facts is None else facts
    name = config_store.assistant_name(cfg)
    profile = config_store.load_profile()
    from src.core import startup
    return {"needed": needed(cfg), "step": (cfg.get("setup", {}) or {}).get("step") or STEPS[0], "steps": list(STEPS),
            "name": name, "name_ideas": list(NAME_IDEAS), "facts": facts,
            "groups": catalogue_view(name, facts, cfg), "models_dir": str(paths.models_dir()),
            "user_name": ((profile.get("user") or {}).get("name") or ""),
            "folders": [p.get("path") for p in (profile.get("projects") or {}).values() if p.get("path")],
            "presets": [{"id": k, "title": v["title"], "text": models.text(v["text"], name)} for k, v in PRESETS.items()],
            "preset": current_preset(cfg), "startup": startup.is_startup_enabled(),
            "microphone": (cfg.get("audio", {}) or {}).get("input_device_index"),
            "speak": bool((cfg.get("tts", {}) or {}).get("enabled", True)),
            "provider": ((cfg.get("llm", {}) or {}).get("main") or {}).get("provider", "claude_code")}


def use_model(option_id: str, facts: dict) -> dict:
    """Settings › Models › Use this: switch to an option (its settings, remembered as the group's choice).
    Returns {"ok", "download": whether its files still need downloading}."""
    group, option = models.find(option_id)
    updates = models.settings_for(group["id"], option_id, facts)
    cfg = config_store.load_config()
    saved = dict((cfg.get("setup", {}) or {}).get("models", {}) or {})
    saved[group["id"]] = option_id
    updates["setup.models"] = saved
    config_store.update_config(updates)
    return {"ok": True, "download": models.status(option)["state"] not in ("installed", "builtin")}


def _project_key(folder: Path, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", folder.name.lower()).strip("-") or "folder"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def save_step(step: str, data: dict, facts: Optional[dict] = None) -> dict:
    """Save one screen. Returns {"ok": True, ...} or {"ok": False, "error": plain sentence}."""
    if step not in STEPS and step != "finish":
        return {"ok": False, "error": f"Unknown setup step {step!r}"}
    updates, result = {}, {"ok": True}
    try:
        if step == "name":
            error, _ = config_store.check_assistant_name(data.get("name", ""))
            if error:
                return {"ok": False, "error": error}
            updates["assistant.name"] = " ".join(str(data["name"]).split())
        elif step == "models":
            facts = hardware.detect() if facts is None else facts
            picks = dict(data.get("choices") or {})
            for group_id, option_id in picks.items():
                updates.update(models.settings_for(group_id, option_id, facts))
            cfg = config_store.load_config()
            saved = dict((cfg.get("setup", {}) or {}).get("models", {}) or {})
            saved.update(picks)
            updates["setup.models"] = saved
            result["downloads"] = [o for o in picks.values() if models.status(models.find(o)[1])["state"] != "builtin"]
        elif step == "microphone":
            device = data.get("device")
            updates["audio.input_device_index"] = None if device in (None, "", "null") else int(device)
        elif step == "voice":
            if data.get("option"):
                facts = hardware.detect() if facts is None else facts
                updates.update(models.settings_for("tts", data["option"], facts))
                cfg = config_store.load_config()
                saved = dict((cfg.get("setup", {}) or {}).get("models", {}) or {})
                saved["tts"] = data["option"]
                updates["setup.models"] = saved
                result["downloads"] = [data["option"]] if models.status(models.find(data["option"])[1])["state"] \
                    not in ("builtin", "installed") else []
            updates["tts.enabled"] = bool(data.get("speak", True))
        elif step == "about":
            _save_about(data)
        elif step == "permissions":
            preset = data.get("preset", "everyday")
            if preset not in PRESETS:
                return {"ok": False, "error": f"Unknown choice {preset!r}"}
            cfg = config_store.load_config()
            perms = {k: v for k, v in ((cfg.get("tools", {}) or {}).get("permissions", {}) or {}).items()
                     if not (v == "deny" and k in DESKTOP_TOOLS + WORK_TOOLS)}
            perms.update({tool: "deny" for tool in PRESETS[preset]["deny"]})
            updates["tools.permissions"] = perms
            updates["tools.preset"] = preset
        elif step == "startup":
            from src.core import startup
            startup.set_startup(bool(data.get("autostart", False)))
        elif step == "finish":
            updates["setup.complete"] = True
        if step != "finish":
            updates["setup.step"] = step
        config_store.update_config(updates)
    except (ValueError, KeyError, OSError) as e:
        return {"ok": False, "error": str(e)}
    return result


def _save_about(data: dict) -> None:
    profile_path = paths.profile_file()
    user_file = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    name = " ".join(str(data.get("user_name") or "").split())
    user = dict(user_file.get("user") or {})
    if name:
        user["name"] = name
    else:
        user.pop("name", None)
    if user:
        user_file["user"] = user
    projects = dict(user_file.get("projects") or {})
    known = {str(Path(p.get("path", "")).resolve()).lower() for p in projects.values() if p.get("path")}
    for folder in data.get("folders") or []:
        path = Path(folder)
        if not path.is_dir() or str(path.resolve()).lower() in known:
            continue
        projects[_project_key(path, set(projects))] = {"path": str(path.resolve()), "description": path.name}
        known.add(str(path.resolve()).lower())
    if projects:
        user_file["projects"] = projects
    config_store.save_profile(user_file)


# ---------------------------------------------------------------------- Sign in with Claude

def find_claude(cfg: Optional[dict] = None) -> Optional[str]:
    """The Claude Code CLI: the configured path, PATH, or the official installer's folder."""
    cfg = config_store.load_config() if cfg is None else cfg
    configured = ((cfg.get("llm", {}) or {}).get("providers", {}) or {}).get("claude_code", {}).get("cli_path") or "claude"
    for candidate in (configured, str(Path.home() / ".local" / "bin" / "claude.exe")):
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def claude_status(cli: Optional[str] = None) -> dict:
    """{"installed", "signed_in", "email", "plan", "error"} from `claude auth status --json` (Jarvis never
    reads Claude's credentials; the CLI keeps them)."""
    cli = cli or find_claude()
    if not cli:
        return {"installed": False, "signed_in": False}
    try:
        res = winproc.run_hidden([cli, "auth", "status", "--json"], timeout=30, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
        data = json.loads(res.stdout or "{}")
    except Exception as e:
        return {"installed": True, "signed_in": False, "error": str(e)}
    return {"installed": True, "signed_in": bool(data.get("loggedIn")), "email": data.get("email") or "",
            "plan": data.get("subscriptionType") or "", "method": data.get("authMethod") or ""}


def start_claude_login(cli: Optional[str] = None):
    """Start the browser sign-in (`claude auth login --claudeai`) without a window; returns the process.
    The caller polls claude_status() until signed in, and kills the process on cancel."""
    cli = cli or find_claude()
    if not cli:
        raise FileNotFoundError("Claude Code is not installed")
    return winproc.popen_hidden([cli, "auth", "login", "--claudeai"])
