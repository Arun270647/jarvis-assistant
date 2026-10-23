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
import tempfile
import threading
from pathlib import Path
from typing import Optional

from src.core import paths

DEFAULTS_PATH = paths.shipped_config_dir() / "defaults.json"
PROFILE_DEFAULTS_PATH = paths.shipped_config_dir() / "profile_defaults.json"
CONFIG_VERSION = 1
ENV_PREFIX = "JARVIS__"

# config_version -> function upgrading a user config.json from that version to the next one.
MIGRATIONS = {}

_lock = threading.Lock()
_MISSING = object()

# Editable settings: dotted path -> (type, constraint). Constraint is a tuple of
# allowed values, a (min, max) range for numbers, or None.
EDITABLE = {
    "llm.claude.model": (str, None),
    "llm.claude.effort": (str, ("low", "medium", "high", "xhigh", "max")),
    "llm.claude.timeout_seconds": (int, (5, 600)),
    "llm.claude.session_reset_after": (int, (1, 10000)),
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
    "tts.engine": (str, ("chatterbox", "piper")),
    "tools.approval_timeout_ms": (int, (3000, 120000)),
    "tools.default_timeout_s": (int, (1, 600)),
    "tools.permissions": (dict, None),
    "claude_code.require_confirmation": (bool, None),
    "claude_code.timeout_seconds": (int, (10, 3600)),
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
    "phone.enabled": (bool, None),
    "phone.port": (int, (1024, 65535)),
    "phone.bind": (str, None),
    "phone.speak_replies": (bool, None),
    "phone.allow_screenshot": (bool, None),
    "phone.approval_timeout_s": (int, (30, 1800)),
    "gui.start_minimized": (bool, None),
    "gui.close_to_tray": (bool, None),
    "gui.autostart_pipeline": (bool, None),
}
# Settings where null is a valid choice (null microphone = the system default).
NULLABLE = {"audio.input_device_index"}

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
    """The shipped profile. Apps with an absolute path (after %VAR% expansion) are offered only when installed."""
    prof = _read_json(PROFILE_DEFAULTS_PATH)
    apps = {}
    for key, app in (prof.get("apps") or {}).items():
        path = os.path.expandvars(app.get("path", ""))
        if not Path(path).is_absolute() or Path(path).exists():
            apps[key] = dict(app, path=path)
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
        if typ is str and key in ("llm.claude.model",) and not value.strip():
            errors.append(f"{key}: cannot be empty")
        if key == "phone.bind" and value.strip() in ("0.0.0.0", "::", "*", ""):
            errors.append("phone.bind: use tailscale, lan or one address; listening on every interface is not allowed")
    return errors


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
