"""Settings and the user profile: shipped defaults with the user's own files on top.

Settings, merged in this order (later wins):
  1. config/defaults.json in the app folder (shipped, read-only, generic)
  2. config.json in the user's data folder (src.core.paths.config_file()): only what the user changed
  3. environment overrides JARVIS__<section>__<key>=<JSON value>, e.g. JARVIS__stt__device=cuda or
     JARVIS__dry_run=true (for development and CI; never written to disk)
load_config() returns the merged settings. update_config() validates a Settings change and writes only the
keys that differ from the defaults, atomically (a crash mid-save cannot corrupt the file), so later releases
can change a default the user never touched. The user file carries config_version; migrate_user_config()
upgrades older files at start-up.

The profile works the same way: config/profile_defaults.json (common apps, media keys, system commands)
with the user's user_profile.json on top. Every section is optional; a missing or empty profile works.
"""
import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional

from src.core import paths

DEFAULTS_PATH = paths.shipped_config_dir() / "defaults.json"
PROFILE_DEFAULTS_PATH = paths.shipped_config_dir() / "profile_defaults.json"
CONFIG_VERSION = 5
ENV_PREFIX = "JARVIS__"


def _v1_to_v2(raw: dict) -> dict:
    """Several AI providers: llm.claude -> llm.providers.claude_code, greeting.model -> its utility_model,
    claude_code (delegation) -> coding_agent, its yes/no words -> voice_approval, and the renamed
    delegate_to_claude tool in tools.permissions."""
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    old = llm.pop("claude", None)
    cc = {}
    if isinstance(old, dict):
        cc.update({k: old[k] for k in ("model", "effort", "cli_path", "timeout_seconds", "session_reset_after")
                   if k in old})
    greeting = raw.get("greeting")
    if isinstance(greeting, dict) and "model" in greeting:
        cc["utility_model"] = greeting.pop("model")
        if not greeting:
            raw.pop("greeting")
    if cc:
        llm.setdefault("providers", {}).setdefault("claude_code", {}).update(cc)
    if llm:
        raw["llm"] = llm
    else:
        raw.pop("llm", None)
    old = raw.pop("claude_code", None)
    if isinstance(old, dict):
        agent = {k: old[k] for k in ("default_project", "timeout_seconds", "capture_output", "summarize_for_tts",
                                     "require_confirmation") if k in old}
        if old.get("enabled") is False:
            agent["cli"] = "off"
        elif old.get("cli_path") not in (None, "", "claude"):
            agent["cli"] = "custom"
            agent["custom_command"] = [old["cli_path"]] + list(old.get("cli_args") or ["-p"])
        voice = {new: old[k] for k, new in (("confirmation_window_ms", "window_ms"),
                                            ("confirm_affirmative", "affirmative"),
                                            ("confirm_negative", "negative")) if k in old}
        if agent:
            raw["coding_agent"] = agent
        if voice:
            raw["voice_approval"] = voice
    perms = (raw.get("tools") or {}).get("permissions") if isinstance(raw.get("tools"), dict) else None
    if isinstance(perms, dict) and "delegate_to_claude" in perms:
        perms["delegate_to_coding_agent"] = perms.pop("delegate_to_claude")
    return raw


def _v2_to_v3(raw: dict) -> dict:
    """The setup wizard arrived: someone who already has settings has set Jarvis up, so it doesn't show again."""
    raw.setdefault("setup", {})["complete"] = True
    return raw


# The wizard's per-tool denies before tool groups (Phase 3), dropped by _v3_to_v4.
_OLD_PRESET_DENIES = {
    "everyday": ("desktop_snapshot", "desktop_screenshot", "desktop_click", "desktop_type", "desktop_keys",
                 "desktop_scroll", "desktop_focus", "edit_file", "write_file", "delete_file", "run_command",
                 "process_output", "stop_process", "open_terminal", "terminal_type", "terminal_key", "terminal_read",
                 "check_process", "http_check", "send_email", "delegate_to_coding_agent"),
    "work": ("desktop_snapshot", "desktop_screenshot", "desktop_click", "desktop_type", "desktop_keys",
             "desktop_scroll", "desktop_focus"),
    "full": (),
}
# What each of those choices could do, as tool groups (the groups as they were in version 4).
_V4_GROUPS = ("workspace_read", "workspace_write", "terminals", "desktop", "system", "email", "coding_agent")
_V4_PRESET_GROUPS = {
    "everyday": ("workspace_read", "system"),
    "work": ("workspace_read", "system", "workspace_write", "terminals", "email", "coding_agent"),
    "full": _V4_GROUPS,
}


def _v3_to_v4(raw: dict) -> dict:
    """Tool groups arrived, with the risky ones off on new installs. An existing install keeps what it could
    do: the wizard's choice becomes groups (its per-tool denies go), and without one every group stays on."""
    tools = raw.get("tools") if isinstance(raw.get("tools"), dict) else {}
    if "groups" in tools:
        return raw
    preset = tools.get("preset")
    if preset in _V4_PRESET_GROUPS:
        tools["groups"] = {g: g in _V4_PRESET_GROUPS[preset] for g in _V4_GROUPS}
        perms = tools.get("permissions")
        if isinstance(perms, dict):
            for tool in _OLD_PRESET_DENIES.get(preset, ()):
                if perms.get(tool) == "deny":
                    perms.pop(tool)
    else:
        tools["groups"] = {g: True for g in _V4_GROUPS}
    raw["tools"] = tools
    return raw


def _v4_to_v5(raw: dict) -> dict:
    """The phone app and its server were removed: their settings go."""
    raw.pop("phone", None)
    return raw


# config_version -> function upgrading a user config.json from that version to the next one.
MIGRATIONS = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5}

_lock = threading.Lock()
_MISSING = object()

# Editable settings: dotted path -> (type, constraint). Constraint is a tuple of
# allowed values, a (min, max) range for numbers, or None.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EDITABLE = {
    "assistant.name": (str, None),
    "setup.complete": (bool, None),
    "setup.step": (str, None),
    "setup.models": (dict, None),
    "tools.preset": (str, ("", "everyday", "work", "full")),
    "llm.main.provider": (str, None),
    "llm.utility.provider": (str, None),
    "llm.utility.model": (str, None),
    "llm.timeout_seconds": (int, (5, 3600)),
    "llm.max_tokens": (int, (256, 128000)),
    "llm.history_budget_tokens": (int, (2000, 2000000)),
    "llm.daily_budget_usd": (float, (0, 100000)),
    "llm.warmup": (bool, None),
    "agent.full_access": (bool, None),
    "agent.max_turns": (int, (1, 200)),
    "agent.max_seconds": (int, (10, 3600)),
    "agent.memory_context_items": (int, (0, 30)),
    "agent.max_tool_calls_per_request": (int, (1, 200)),
    "agent.task.max_tool_calls": (int, (5, 500)),
    "agent.task.max_seconds": (int, (60, 14400)),
    "agent.task.max_verify_attempts": (int, (1, 10)),
    "agent.task.speak_progress": (bool, None),
    "audio.input_device_index": (int, (0, 512)),
    "stt.model_path": (str, None),
    "stt.device": (str, ("cuda", "cpu", "auto")),
    "stt.compute_type": (str, ("int8", "int8_float16", "float16", "float32")),
    "stt.beam_size": (int, (1, 10)),
    "greeting.enabled": (bool, None),
    "gui.startup_view": (str, ("tray", "orb")),
    "pipeline.silence_timeout_ms": (int, (150, 3000)),
    "pipeline.max_recording_ms": (int, (1000, 30000)),
    "tts.enabled": (bool, None),
    "tts.piper.voice": (str, None),
    "tts.engine": (str, ("chatterbox", "piper", "system")),
    "tts.fallback": (str, ("system", "none")),
    "tts.system.voice": (str, None),
    "tts.chatterbox.python": (str, None),
    "tts.chatterbox.hf_home": (str, None),
    "tts.chatterbox.torch_home": (str, None),
    "tts.chatterbox.device": (str, ("cpu", "cuda")),
    "tts.system.rate": (int, (-10, 10)),
    "tools.approval_timeout_ms": (int, (3000, 120000)),
    "tools.default_timeout_s": (int, (1, 600)),
    "tools.permissions": (dict, None),
    "tools.groups": (dict, None),
    "tools.untrusted_asks": (bool, None),
    "tools.extensions": (list, None),
    "voice_approval.high_risk": (bool, None),
    "apps.discover": (bool, None),
    "updates.auto_check": (bool, None),
    "tools.profile": (str, ("auto", "full", "core", "custom")),
    "tools.custom_list": (list, None),
    "coding_agent.cli": (str, ("claude", "codex", "aider", "custom", "off")),
    "coding_agent.custom_command": (list, None),
    "coding_agent.default_project": (str, None),
    "coding_agent.require_confirmation": (bool, None),
    "coding_agent.timeout_seconds": (int, (10, 3600)),
    "dry_run": (bool, None),
    "gui.orb_style": (str, ("hud", "reticle", "waveform", "particles")),
    "email.enabled": (bool, None),
    "email.preset": (str, ("zoho-in", "zoho", "gmail", "outlook", "custom")),
    "email.smtp_host": (str, None),
    "email.smtp_port": (int, (1, 65535)),
    "email.security": (str, ("ssl", "starttls", "none")),
    "email.username": (str, None),
    "email.from_address": (str, None),
    "email.from_name": (str, None),
    "email.signature": (str, None),
    "email.trusted_recipients": (list, None),
    "chrome.default_profile": (str, None),
    "chrome.blocked_domains": (list, None),
    "gui.start_minimized": (bool, None),
    "gui.close_to_tray": (bool, None),
    "gui.autostart_pipeline": (bool, None),
}
# Settings where null is a valid choice (null microphone = the system default, null budget = none).
NULLABLE = {"audio.input_device_index", "llm.daily_budget_usd"}


DEFAULT_ASSISTANT_NAME = "Jarvis"
# Letters (any alphabet), with single spaces, hyphens or apostrophes between them.
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-’][^\W\d_]+)*$")
# Words people say all the time: as a name they would wake or address the assistant by accident.
COMMON_WORDS = {"hey", "hi", "hello", "okay", "ok", "yes", "no", "stop", "computer", "assistant", "please",
                "thanks", "thank you", "the", "what", "open", "play", "siri", "alexa", "google", "cortana"}


def check_assistant_name(name: str) -> tuple:
    """(error, warning) for a proposed assistant name; each is None or one plain sentence."""
    name = " ".join(str(name or "").split())
    if not name:
        return "Give your assistant a name.", None
    if not 2 <= len(name) <= 20:
        return "Use 2 to 20 characters.", None
    if not _NAME_RE.match(name):
        return "Use letters, with spaces, hyphens or apostrophes between them.", None
    if name.lower() in COMMON_WORDS:
        return None, f"\u201c{name}\u201d is a word people say a lot, so it may be picked up by accident."
    return None, None


def assistant_name(cfg: Optional[dict] = None) -> str:
    """What the user calls the assistant (assistant.name); the product name until they pick another."""
    cfg = load_config() if cfg is None else cfg
    name = " ".join(str(((cfg.get("assistant") or {}).get("name")) or "").split())
    return name if name and check_assistant_name(name)[0] is None else DEFAULT_ASSISTANT_NAME


def with_name(text: str, name: str) -> str:
    """Fill the {name} placeholder in a setting such as the greeting prefix."""
    return str(text or "").replace("{name}", name)


def _provider_editables() -> None:
    """Per-provider settings, for every provider the shipped defaults define (null vision = guess)."""
    try:
        shipped = (_read_json(DEFAULTS_PATH).get("llm", {}) or {}).get("providers", {}) or {}
    except Exception:
        return
    specs = {"model": (str, None), "utility_model": (str, None), "base_url": (str, None), "cli_path": (str, None),
             "timeout_seconds": (int, (5, 3600)), "session_reset_after": (int, (1, 10000)),
             "effort": (str, EFFORTS), "vision": (bool, None)}
    for name, entry in shipped.items():
        for key, spec in specs.items():       # only what the provider defines (others use the llm.* value)
            if key in entry:
                EDITABLE[f"llm.providers.{name}.{key}"] = spec
                if key == "vision":
                    NULLABLE.add(f"llm.providers.{name}.{key}")

PERMISSION_VALUES = ("allow", "ask", "deny")


def _read_json(path: Path) -> dict:
    """The JSON object in `path`, or {} when the file does not exist."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _deep_merge(base: dict, over: dict) -> dict:
    """`over` merged into `base` (nested dicts merge key by key, anything else replaces). Returns base."""
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _diff(cfg: dict, base: dict) -> dict:
    """The parts of `cfg` that differ from `base`: what the user's file has to store."""
    out = {}
    for k, v in cfg.items():
        b = base.get(k, _MISSING)
        if isinstance(v, dict) and isinstance(b, dict):
            sub = _diff(v, b)
            if sub:
                out[k] = sub
        elif v != b:
            out[k] = copy.deepcopy(v)
    return out


def _env_overrides() -> dict:
    out = {}
    for name, raw in os.environ.items():
        if not name.upper().startswith(ENV_PREFIX) or len(name) <= len(ENV_PREFIX):
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw                       # a bare string: JARVIS__stt__device=cpu
        # Windows upper-cases environment names; every config key is lower case.
        _set_path(out, ".".join(name[len(ENV_PREFIX):].lower().split("__")), value)
    return out


def defaults() -> dict:
    """The shipped defaults (a fresh copy)."""
    return _read_json(DEFAULTS_PATH)


def user_config(path: Optional[Path] = None) -> dict:
    """Only what the user changed (their config.json), without defaults."""
    return _read_json(Path(path) if path else paths.config_file())


def load_config(path: Optional[Path] = None) -> dict:
    """The settings in effect: defaults <- the user's config.json (`path`) <- environment overrides.
    Never writes."""
    cfg = defaults()
    _deep_merge(cfg, user_config(path))
    return _deep_merge(cfg, _env_overrides())


def profile_defaults() -> dict:
    """The shipped profile. Apps with an absolute path (after %VAR% expansion) are offered only when installed;
    common apps found in the Start Menu are added (src/core/app_discovery.py, unless apps.discover is off)."""
    prof = _read_json(PROFILE_DEFAULTS_PATH)
    apps = {}
    for key, app in (prof.get("apps") or {}).items():
        path = os.path.expandvars(app.get("path", ""))
        if not Path(path).is_absolute() or Path(path).exists():
            apps[key] = dict(app, path=path)
    if (load_config().get("apps", {}) or {}).get("discover", True):
        try:
            from src.core.app_discovery import discover
            for key, app in discover().items():
                apps.setdefault(key, app)
        except Exception as e:            # a broken shortcut folder must never stop the profile from loading
            print(f"[APPS] Start Menu discovery skipped: {e}")
    prof["apps"] = apps
    return prof


def load_profile(path: Optional[Path] = None) -> dict:
    """Who the user is (user, contacts, projects) plus apps, media keys and system commands: the shipped
    profile with the user's user_profile.json (`path`) on top. Sections merge entry by entry, so the user's
    "chrome" replaces the default one and their own apps are added to the common ones."""
    prof = profile_defaults()
    for section, value in _read_json(Path(path) if path else paths.profile_file()).items():
        if isinstance(value, dict) and isinstance(prof.get(section), dict):
            prof[section].update(value)
        else:
            prof[section] = value
    return prof


def save_profile(user_profile: dict, path: Optional[Path] = None) -> None:
    """Write the user's own profile file (only their sections; the shipped profile stays underneath)."""
    path = Path(path) if path else paths.profile_file()
    with _lock:
        _atomic_write(path, user_profile)


def get_path(cfg: dict, dotted: str, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _set_path(cfg: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def validate_update(updates: dict) -> list:
    """Return a list of error strings for a {dotted.key: value} update."""
    errors = []
    for key, value in updates.items():
        spec = EDITABLE.get(key)
        if spec is None:
            errors.append(f"{key}: not an editable setting")
            continue
        if value is None and key in NULLABLE:
            continue
        typ, constraint = spec
        if typ is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if typ is list and isinstance(value, list) and not all(isinstance(x, str) for x in value):
            errors.append(f"{key}: expected a list of strings")
            continue
        if typ is int and isinstance(value, bool) or not isinstance(value, typ):
            errors.append(f"{key}: expected {typ.__name__}, got {type(value).__name__}")
            continue
        if isinstance(constraint, tuple) and constraint and isinstance(constraint[0], str):
            if value not in constraint:
                errors.append(f"{key}: must be one of {', '.join(constraint)}")
        elif isinstance(constraint, tuple) and len(constraint) == 2:
            lo, hi = constraint
            if not (lo <= value <= hi):
                errors.append(f"{key}: must be between {lo} and {hi}")
        if key == "tools.permissions":
            for tool, perm in value.items():
                if perm not in PERMISSION_VALUES:
                    errors.append(f"tools.permissions.{tool}: must be allow, ask or deny")
        if key == "tools.groups":
            switchable = (defaults().get("tools", {}) or {}).get("groups", {})     # = src/tools/groups.py (tested)
            for gid, on in value.items():
                if gid not in switchable:
                    errors.append(f"tools.groups.{gid}: not a group that can be switched")
                elif not isinstance(on, bool):
                    errors.append(f"tools.groups.{gid}: must be true or false")
        if key == "tools.extensions":
            found = {f.stem for f in (paths.APP_ROOT / "tools_ext").glob("*.py") if not f.name.startswith("_")}
            missing = [e for e in value if e not in found]
            if missing:
                errors.append(f"tools.extensions: no such extension in tools_ext: {', '.join(missing)}")
        if key == "assistant.name" and check_assistant_name(value)[0]:
            errors.append(f"assistant.name: {check_assistant_name(value)[0]}")
        if key == "llm.main.provider" and value not in _provider_names():
            errors.append(f"{key}: unknown provider {value!r}")
        if key == "llm.utility.provider" and value and value not in _provider_names():
            errors.append(f"{key}: unknown provider {value!r}")
        if key.startswith("llm.providers.") and key.endswith(".model") and not value.strip()                 and get_path(defaults(), key):
            errors.append(f"{key}: cannot be empty")
    return errors


def _provider_names() -> set:
    return set(((load_config().get("llm", {}) or {}).get("providers", {}) or {}))


def update_config(updates: dict, path: Optional[Path] = None) -> dict:
    """Validate and apply {dotted.key: value} updates; the user's file keeps only what differs from the
    defaults. Returns the new settings."""
    errors = validate_update(updates)
    if errors:
        raise ValueError("; ".join(errors))
    path = Path(path) if path else paths.config_file()
    with _lock:
        base = defaults()
        merged = _deep_merge(copy.deepcopy(base), user_config(path))
        for key, value in updates.items():
            if EDITABLE[key][0] is float and value is not None:
                value = float(value)
            _set_path(merged, key, value)
        out = _diff(merged, base)
        out["config_version"] = CONFIG_VERSION
        _atomic_write(path, out)
    return load_config(path)


def migrate_user_config(path: Optional[Path] = None) -> None:
    """Bring the user's config.json up to CONFIG_VERSION (run once at start-up). Without a file there is
    nothing to do: the defaults apply until the user changes a setting."""
    path = Path(path) if path else paths.config_file()
    with _lock:
        raw = user_config(path)
        if not raw:
            return
        version = int(raw.get("config_version") or 1)
        if version >= CONFIG_VERSION:
            return
        while version < CONFIG_VERSION:
            raw = MIGRATIONS[version](raw)
            version += 1
        raw["config_version"] = version
        _atomic_write(path, raw)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_provider_editables()
