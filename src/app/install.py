"""Install for me (the setup wizard's last screen and Settings › About; the Windows zip and Jarvis.app).

Windows: copies the Jarvis folder from wherever the zip was extracted (usually Downloads) to
%LOCALAPPDATA%\\Programs\\Jarvis, adds a Start Menu shortcut (and a Desktop one if asked) and an entry in Settings ›
Apps, points the start-up shortcut at the new copy, then starts the installed copy once this one has quit. No
administrator rights are needed.

macOS: copies Jarvis.app into /Applications (~/Applications when that isn't writable) with ditto, and starts it from
there. A Jarvis.app opened straight from Downloads runs from a read-only copy macOS makes (App Translocation), which
can't update itself; the installed copy can, and so can one opened straight from the downloaded disk image, which is
read-only (Install for me then ejects the image and deletes the .dmg). macOS already let the user open this app, so
the installed copy has the download's quarantine flag removed and opens without asking again.

The extracted copy is deleted afterwards if the user agrees. The settings, memories and models live in the user's
data folders, so the installed copy starts with everything already set up.
"""
import os
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

from src.core import paths
from src.version import __version__

APP_NAME = "Jarvis"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"
SKIP = (".update", ".old", "__pycache__")
IS_MAC = sys.platform == "darwin"


def target() -> Path:
    if IS_MAC:
        applications = Path("/Applications")
        return (applications if os.access(applications, os.W_OK) else Path.home() / "Applications") / f"{APP_NAME}.app"
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


def is_jarvis_app(app: Path) -> bool:
    """A Jarvis.app built by packaging/build_macos.py, and nothing else."""
    app = Path(app)
    return app.suffix == ".app" and (app / "Contents" / "MacOS" / APP_NAME).is_file() and \
        (app / "Contents" / "Resources" / "runtime").is_dir() and (app / "Contents" / "Resources" / "app").is_dir()


def original_location(app: Path) -> Path:
    """Where the user's Jarvis.app really is when macOS runs a translocated (read-only) copy of it."""
    if "/AppTranslocation/" not in str(app):
        return app
    try:
        import ctypes
        import objc
        from Foundation import NSURL
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        security.SecTranslocateCreateOriginalPathForURL.restype = ctypes.c_void_p
        security.SecTranslocateCreateOriginalPathForURL.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        url = NSURL.fileURLWithPath_(str(app))
        original = security.SecTranslocateCreateOriginalPathForURL(objc.pyobjc_id(url), None)
        if original:
            return Path(str(objc.objc_object(c_void_p=original).path()))
    except Exception as e:
        print(f"[INSTALL] Couldn't find where this Jarvis.app came from: {e}")
    return app


def disk_image(app: Path) -> Optional[tuple]:
    """(mount point, image file) when the app is on an attached disk image, such as the downloaded .dmg; else None."""
    if not Path(app).as_posix().startswith("/Volumes/"):
        return None
    try:
        from src.core import winproc
        out = winproc.run_hidden(["/usr/bin/hdiutil", "info", "-plist"], timeout=30, check=True,
                                 capture_output=True).stdout
        for image in plistlib.loads(out).get("images", []):
            for entity in image.get("system-entities", []):
                mount = entity.get("mount-point")
                if mount and Path(app).is_relative_to(mount):
                    return Path(mount), Path(image["image-path"])
    except Exception as e:
        print(f"[INSTALL] Couldn't tell which disk image Jarvis runs from: {e}")
    return None


def _installed_place(root: Path) -> bool:
    """macOS: the app is already in an Applications folder (Install for me has nothing to do)."""
    return root.parent in (Path("/Applications"), Path.home() / "Applications")


def state() -> dict:
    """What the wizard and Settings › About show: whether Install for me applies, and where it would go."""
    root = paths.install_root()
    dest = target()
    if IS_MAC:
        source = original_location(root) if root else None
        installed_here = bool(root) and (_installed_place(source) or source.resolve() == dest.resolve())
        image = disk_image(source) if root and not installed_here else None
        return {"available": bool(root) and not installed_here, "installed": installed_here,
                "from": str(image[1] if image else source.parent if source else ""), "to": str(dest.parent)}
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
    if IS_MAC:
        return _install_mac(remove_copy, pid)
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


def _install_mac(remove_copy: bool, pid: Optional[int]) -> dict:
    """Copy Jarvis.app into Applications, then (after this copy quits) open it there and delete the download (or
    eject the disk image it came on and delete the .dmg)."""
    from src.core import startup, winproc
    running, dest = paths.install_root(), target()
    original = original_location(running)
    try:
        if dest.exists():
            if not is_jarvis_app(dest):
                return {"ok": False, "error": f"{dest} already exists and isn't Jarvis, so it was left alone."}
            shutil.rmtree(dest)                 # an older installed copy that isn't running (this one is the download)
        dest.parent.mkdir(parents=True, exist_ok=True)
        winproc.run_hidden(["/usr/bin/ditto", str(running), str(dest)], timeout=600, check=True,
                           capture_output=True)
        winproc.run_hidden(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(dest)], timeout=120,
                           capture_output=True)
        if startup.is_startup_enabled():
            startup.set_startup(True, dest)
    except (OSError, winproc.subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"Couldn't install into {dest.parent}: {e}"}
    image = disk_image(original)
    if image:
        remove = image[1] if remove_copy else None
        cleanup = [f'/usr/bin/hdiutil detach "{image[0]}" -quiet'] + ([f'rm -f "{remove}"'] if remove else [])
    else:
        remove = original if remove_copy and original != running and is_jarvis_app(original) else None
        cleanup = [f'rm -rf "{remove}"'] if remove else []
    lines = ["#!/bin/sh", f"while kill -0 {pid or os.getpid()} 2>/dev/null; do sleep 0.5; done",
             f'/usr/bin/open "{dest}"'] + cleanup + ['rm -f "$0"']
    fd, script = tempfile.mkstemp(prefix="jarvis-install-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    winproc.popen_hidden(["/bin/sh", script])
    return {"ok": True, "to": str(dest), "shortcuts": [], "removing": str(remove) if remove else ""}


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
