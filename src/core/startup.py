"""Start Jarvis when the user signs in (Settings › Start-up), through src.platform.

Windows: a shortcut in the Startup folder that starts the app as the floating orb: Jarvis.exe when installed,
pythonw with jarvis_gui.pyw when run from source. macOS: a LaunchAgent that opens Jarvis.app (or runs python with
jarvis_gui.pyw from source).
"""
import sys
from pathlib import Path
from typing import Optional

from src.core import paths
from src.core.paths import APP_ROOT
from src.core.winproc import pythonw_executable
from src.platform import current

GUI_SCRIPT = APP_ROOT / "jarvis_gui.pyw"
ICON = APP_ROOT / "src" / "gui" / "assets" / "jarvis.ico"     # scripts/make_logo.py


def launch_command(root: Optional[Path] = None) -> dict:
    """How a shortcut starts this copy of Jarvis (root: a Windows Jarvis folder or a Jarvis.app, by default
    this one)."""
    root = root or paths.install_root()
    if sys.platform == "darwin":
        if root:
            return {"program": "/usr/bin/open", "arguments": f'-a "{root}" --args', "working_dir": str(root.parent),
                    "icon": ""}
        return {"program": sys.executable, "arguments": f'"{GUI_SCRIPT}"', "working_dir": str(APP_ROOT), "icon": ""}
    if root:
        exe = root / "Jarvis.exe"
        return {"program": str(exe), "arguments": "", "working_dir": str(root), "icon": f"{exe},0"}
    return {"program": pythonw_executable(), "arguments": f'"{GUI_SCRIPT}"', "working_dir": str(APP_ROOT),
            "icon": f"{ICON},0" if ICON.exists() else ""}


def is_startup_enabled() -> bool:
    return current().autostart_enabled()


def set_startup(enabled: bool, root: Optional[Path] = None) -> bool:
    """Turn start-at-sign-in on or off. Returns the new state."""
    c = launch_command(root)
    return current().set_autostart(enabled, c["program"], (c["arguments"] + " --minimized").strip(), c["working_dir"],
                                   "Jarvis voice assistant", c["icon"])
