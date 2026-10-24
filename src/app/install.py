"""Install for me (the setup wizard's last screen and Settings › About; Windows zip only).

Copies the Jarvis folder from wherever the zip was extracted (usually Downloads) to %LOCALAPPDATA%\\Programs\\Jarvis,
adds a Start Menu shortcut (and a Desktop one if asked) and an entry in Settings › Apps, points the start-up
shortcut at the new copy, then starts the installed copy once this one has quit. No administrator rights are
needed. The extracted copy is deleted afterwards if the user agrees. The settings, memories and models live in the
user's data folders, so the installed copy starts with everything already set up.
"""
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from src.core import paths
from src.version import __version__

APP_NAME = "Jarvis"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"
SKIP = (".update", ".old", "__pycache__")


def target() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Programs" / APP_NAME


def start_menu_shortcut() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def desktop_shortcut() -> Path:
    from src.platform.windows import WindowsPlatform
    return WindowsPlatform.desktop_dir() / f"{APP_NAME}.lnk"


def is_jarvis_folder(folder: Path) -> bool:
    """A folder from the Jarvis zip, and nothing broader (never a drive root or the user's home)."""
    folder = Path(folder)
    home = Path.home().resolve()
    resolved = folder.resolve()
    if resolved == Path(resolved.anchor) or resolved == home or resolved in home.parents:
        return False
    return (folder / "Jarvis.exe").is_file() and (folder / "runtime").is_dir() and (folder / "app").is_dir()


def state() -> dict:
    """What the wizard and Settings › About show: whether Install for me applies, and where it would go."""
    root = paths.install_root()
    dest = target()
    installed_here = bool(root) and root.resolve() == dest.resolve()
    return {"available": bool(root) and not installed_here, "installed": installed_here,
            "from": str(root) if root else "", "to": str(dest)}


def _register(dest: Path) -> None:
    """The entry in Settings › Apps; Uninstall there opens Settings › About in Jarvis (Jarvis.exe --uninstall)."""
    import winreg
    exe = dest / "Jarvis.exe"
    size_kb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", __version__),
                            ("Publisher", "The Jarvis authors"), ("InstallLocation", str(dest)),
                            ("DisplayIcon", f"{exe},0"), ("UninstallString", f'"{exe}" --uninstall')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def install(desktop: bool = False, remove_copy: bool = True, pid: Optional[int] = None) -> dict:
    """Copy, add shortcuts and the Apps entry, then (after this copy quits) start the installed one. The caller
    quits Jarvis right after a successful result."""
    s = state()
    if not s["available"]:
        return {"ok": False, "error": "Jarvis is already installed, or isn't running from the zip's folder."}
    source, dest = Path(s["from"]), Path(s["to"])
    from src.core import startup, winproc
    from src.platform.windows import WindowsPlatform
    try:
        if dest.exists():
            if not is_jarvis_folder(dest):
                return {"ok": False, "error": f"{dest} already exists and isn't a Jarvis folder, so it was left alone."}
            shutil.rmtree(dest)                 # an older installed copy that isn't running (this one is the zip's)
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(*SKIP))
        command = startup.launch_command(dest)
        shortcuts = [start_menu_shortcut()] + ([desktop_shortcut()] if desktop else [])
        for link in shortcuts:
            WindowsPlatform.create_shortcut(link, command["program"], "", command["working_dir"],
                                            "Jarvis voice assistant", command["icon"])
        _register(dest)
        if startup.is_startup_enabled():
            startup.set_startup(True, dest)
    except OSError as e:
        return {"ok": False, "error": f"Couldn't install into {dest}: {e}"}
    script = _relaunch_script(dest, source if remove_copy and is_jarvis_folder(source) else None, pid or os.getpid())
    winproc.popen_hidden(["cmd", "/d", "/c", str(script)])
    return {"ok": True, "to": str(dest), "shortcuts": [str(p) for p in shortcuts], "removing": str(source) if remove_copy else ""}


def _relaunch_script(dest: Path, remove: Optional[Path], pid: int) -> Path:
    """A throwaway .cmd that waits for this Jarvis to quit, starts the installed copy and deletes the old folder."""
    lines = ["@echo off", "chcp 65001 >nul", ":wait",
             f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
             "if not errorlevel 1 (ping -n 2 127.0.0.1 >nul & goto wait)",
             f'start "" "{dest / "Jarvis.exe"}"']
    if remove:
        lines.append(f'rmdir /s /q "{remove}"')
    lines.append('del "%~f0"')
    fd, path = tempfile.mkstemp(prefix="jarvis-install-", suffix=".cmd")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return Path(path)
