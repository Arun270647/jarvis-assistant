"""Where Jarvis keeps its files.

- The app folder (APP_ROOT) holds the code and the shipped config/ (defaults.json, profile_defaults.json).
  It is never written at run time, so an update can replace it. In the Windows zip it is Jarvis\\app, next to
  Jarvis.exe and the Python runtime; on macOS it is Jarvis.app/Contents/Resources/app. install_root() is that
  Jarvis folder or Jarvis.app.
- The user's data folder, home(): config.json (only the settings that differ from the defaults),
  user_profile.json, jarvis.db and logs/. By default the OS's per-user folder (%APPDATA%\\Jarvis,
  ~/Library/Application Support/Jarvis).
- Large local files: models/ (downloaded speech and voice models) and cache/ (TTS cache, edit backups,
  ...). By default the OS's local folder (%LOCALAPPDATA%\\Jarvis\\models and \\cache; on macOS models/ next to
  the data and ~/Library/Caches/Jarvis).

JARVIS_HOME overrides the data folder; then models/ and cache/ live inside it too, so one folder holds
everything (portable mode, e.g. JARVIS_HOME=.\\userdata for development). A relative JARVIS_HOME is
relative to the app folder.

Everything is computed on each call, so a changed JARVIS_HOME (tests) takes effect immediately.
"""
import os
import re
from pathlib import Path
from typing import Optional

APP_ROOT = Path(__file__).resolve().parents[2]
ENV_HOME = "JARVIS_HOME"


def _override() -> Optional[Path]:
    value = os.environ.get(ENV_HOME, "").strip()
    if not value:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(value)))
    return p if p.is_absolute() else APP_ROOT / p


def _platform():
    from src.platform import current
    return current()


def home() -> Path:
    """The user's data folder (settings, profile, database, logs)."""
    return _override() or _platform().data_dir()


def models_dir() -> Path:
    o = _override()
    return o / "models" if o else _platform().models_dir()


def cache_dir() -> Path:
    o = _override()
    return o / "cache" if o else _platform().cache_dir()


def config_file() -> Path:
    return home() / "config.json"


def profile_file() -> Path:
    return home() / "user_profile.json"


def db_file() -> Path:
    return home() / "jarvis.db"


def log_dir() -> Path:
    return home() / "logs"


def install_root() -> Optional[Path]:
    """The Jarvis folder of the Windows zip (Jarvis.exe, runtime/, app/) or Jarvis.app on macOS
    (Contents/Resources/runtime and app) when running from it; None from source."""
    if APP_ROOT.name != "app":
        return None
    root = APP_ROOT.parent
    if (root / "Jarvis.exe").is_file() and (root / "runtime").is_dir():
        return root
    bundle = APP_ROOT.parents[2]
    if root.name == "Resources" and bundle.suffix == ".app" and (root / "runtime").is_dir():
        return bundle
    return None


def shipped_config_dir() -> Path:
    return APP_ROOT / "config"


def data_dirs() -> list:
    """Every folder Jarvis writes to; the agent may never modify them (WorkspacePolicy)."""
    return [home(), models_dir(), cache_dir()]


def model_path(value: str, kind: str = "") -> Path:
    """A model folder or file from config: absolute paths as given, relative ones inside models_dir()/kind."""
    p = Path(os.path.expandvars(str(value or "")))
    if p.is_absolute():
        return p
    base = models_dir() / kind if kind else models_dir()
    return base / p


_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def whisper_model(value: str):
    """(model, download_root) for faster-whisper. A plain name such as "small" or "large-v3-turbo" is
    downloaded into models/whisper on first use; a path (absolute, or relative to models/whisper) is used
    as a local CTranslate2 model folder."""
    root = models_dir() / "whisper"
    local = model_path(value, "whisper")
    if local.exists() or not _MODEL_NAME.match(str(value or "")):
        return str(local), str(root)
    return str(value), str(root)
