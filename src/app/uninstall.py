"""Uninstall from Settings › About: remove what Jarvis put on this computer, and the user's data if they ask.

plan(delete_data) lists exactly what will go, so the confirmation can show it; run(plan) does it:
- always: the start-at-login shortcut, Start Menu and Desktop shortcuts, the Apps-list entry
  (Install for me creates the last three);
- with "delete my data": the saved keys and passwords (Credential Manager) and the data, models and cache
  folders;
- a copy from the Windows zip (installed or not) also removes its Jarvis folder; a copy run from source never
  deletes itself.
Folders still in use (the database, the log) are deleted by a small script that waits for Jarvis to exit.
A folder is only deleted if it is clearly Jarvis's: never a drive root, the user's home folder or a folder
that contains the app.
"""
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from src.core import config_store, paths

APP_NAME = "Jarvis"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"


def installed_copy() -> bool:
    """True for a copy from the Windows zip (installed or not), false when running from source."""
    return paths.install_root() is not None


def app_folder() -> Optional[Path]:
    """The Jarvis folder to delete after exit: the zip's folder (Jarvis.exe, runtime, app), never source code."""
    from src.app.install import is_jarvis_folder
    root = paths.install_root()
    return root if root and is_jarvis_folder(root) else None


def _data_roots() -> list:
    """The top folders holding the user's data, models and cache (one folder with JARVIS_HOME)."""
    roots = []
    for folder in (paths.home(), paths.models_dir(), paths.cache_dir()):
        top = folder if folder == paths.home() else folder.parent
        if not any(top == r or r in top.parents for r in roots):
            roots = [r for r in roots if top not in r.parents] + [top]
    return roots


def safe_to_delete(folder: Path) -> bool:
    folder = Path(folder).resolve()
    app, user = paths.APP_ROOT.resolve(), Path.home().resolve()
    if folder == Path(folder.anchor) or folder == user or folder in user.parents:
        return False
    if folder == app or folder in app.parents:
        return False
    return True


def _shortcuts() -> list:
    found = []
    try:
        from src.platform.windows import WindowsPlatform
        candidates = [WindowsPlatform.desktop_dir() / f"{APP_NAME}.lnk",
                      Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"]
    except Exception:
        candidates = []
    for p in candidates:
        if p.exists():
            found.append(p)
    return found


def _credential_targets(cfg: dict) -> list:
    from src.agent.providers import credential_target, providers
    targets = [credential_target(p) for p in providers(cfg)]
    email = cfg.get("email", {}) or {}
    user = email.get("username") or email.get("from_address")
    if user:
        targets.append(f"JARVIS/SMTP/{user}")
    return targets


def plan(delete_data: bool, cfg: Optional[dict] = None) -> dict:
    cfg = config_store.load_config() if cfg is None else cfg
    folders = [str(f) for f in _data_roots() if f.exists() and safe_to_delete(f)] if delete_data else []
    folder = app_folder()
    from src.core import startup
    return {"delete_data": delete_data, "startup": startup.is_startup_enabled(),
            "shortcuts": [str(p) for p in _shortcuts()], "apps_entry": _has_apps_entry(),
            "credentials": _credential_targets(cfg) if delete_data else [], "folders": folders,
            "app_folder": str(folder) if folder else None}


def _has_apps_entry() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY))
        return True
    except OSError:
        return False


def run(p: dict, pid: Optional[int] = None) -> dict:
    """Remove everything in the plan; folders go after Jarvis exits (the caller quits right after)."""
    from src.core import credentials, startup
    done = []
    if p.get("startup"):
        startup.set_startup(False)
        done.append("start-up shortcut")
    for s in p.get("shortcuts", []):
        try:
            Path(s).unlink()
            done.append(s)
        except OSError:
            pass
    if p.get("apps_entry"):
        try:
            import winreg
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
            done.append("Apps list entry")
        except OSError:
            pass
    for target in p.get("credentials", []):
        credentials.delete(target)
    folders = [f for f in p.get("folders", []) if safe_to_delete(Path(f))]
    if p.get("app_folder") and app_folder() and Path(p["app_folder"]).resolve() == app_folder().resolve():
        folders.append(p["app_folder"])
    script = cleanup_script(folders, pid or os.getpid()) if folders else None
    if script:
        from src.core import winproc
        winproc.popen_hidden(["cmd", "/d", "/c", str(script)])
    return {"ok": True, "removed": done, "deleting_after_exit": folders}


def cleanup_script(folders: list, pid: int) -> Path:
    """A throwaway .cmd that waits for the Jarvis process to end, deletes the folders, then itself."""
    lines = ["@echo off",
             "chcp 65001 >nul",                                          # UTF-8, for folder names in any language
             ":wait",
             f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
             "if not errorlevel 1 (ping -n 2 127.0.0.1 >nul & goto wait)"]
    lines += [f'rmdir /s /q "{f}"' for f in folders]
    lines.append('del "%~f0"')
    fd, path = tempfile.mkstemp(prefix="jarvis-uninstall-", suffix=".cmd")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return Path(path)
