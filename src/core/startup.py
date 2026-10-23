"""Start Jarvis when the user signs in (Settings › Start-up), through src.platform.

On Windows this is a shortcut in the Startup folder that starts the app as the floating orb.
"""
from src.core.paths import APP_ROOT
from src.core.winproc import pythonw_executable
from src.platform import current

GUI_SCRIPT = APP_ROOT / "jarvis_gui.pyw"
ICON = APP_ROOT / "src" / "gui" / "assets" / "jarvis.ico"     # scripts/make_logo.py


def is_startup_enabled() -> bool:
    return current().autostart_enabled()


def set_startup(enabled: bool) -> bool:
    """Turn start-at-sign-in on or off. Returns the new state."""
    return current().set_autostart(enabled, pythonw_executable(), f'"{GUI_SCRIPT}" --minimized', str(APP_ROOT),
                                   "Jarvis voice assistant", f"{ICON},0" if ICON.exists() else "")
