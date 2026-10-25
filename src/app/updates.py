"""Updates: check the website's latest.json (updates.url), download the new version in the background, and install it
when Jarvis restarts. Runs in the installed Windows copy and in Jarvis.app.

latest.json: {"version": "0.1.1", "notes": "...", "windows": {"url": ".../Jarvis-Setup-0.1.1-windows-x64.exe",
"sha256": "...", "size": 123}, "macos": {"url": ".../Jarvis-0.1.1-macos-arm64.dmg", ...}} (packaging/build_windows.py
and build_macos.py write it).

- check() says whether a newer version exists.
- prepare() downloads it into the cache folder, checks its SHA-256 and gets it ready (stage()), before anyone is
  asked to restart.
- launch_updater() starts the hand-over; Jarvis quits right after.
- after_start() cleans up and returns the result for the app to show.

Windows: the download is the installer itself (packaging/windows/jarvis.iss). It waits in the cache folder's
updates/ with a note of its version (pending.json). On restart, a hidden script (WINDOWS_UPDATER) waits for Jarvis
to quit, runs the installer silently into the same folder, notes the result for the next start and starts Jarvis
again.

macOS: the release is a disk image. It is attached read-only and out of sight, and the new Jarvis.app is copied out
of it with ditto (which keeps its permissions, links and signature) into the cache folder's update/ (a folder inside
the app would break its signature); then the image is detached. A small shell script (MAC_UPDATER) swaps the apps
after Jarvis quits, keeping the old one in the cache folder's update-old/ until the next start.

Run from source (or from a folder Jarvis can't write to), updater_available() is false and a newer version is only
offered as a download.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from src.core import paths
from src.version import __version__

IS_MAC = sys.platform == "darwin"
PLATFORM_KEY = "macos" if IS_MAC else "windows"
PENDING = "pending.json"                  # Windows: {"version", "file"} of the installer waiting in cache/updates
MAC_REQUIRED = ("Contents/Info.plist", "Contents/MacOS/Jarvis", "Contents/Resources/runtime/bin/python3",
                "Contents/Resources/app/jarvis_gui.pyw")
WINDOWS_UPDATER = r"""@echo off
rem Jarvis's Windows updater, written by src/app/updates.py: once Jarvis has quit, run the downloaded installer
rem silently into the same folder, note the result for the next start, and start Jarvis again.
chcp 65001 >nul
set /a tries=0
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if errorlevel 1 goto install
set /a tries+=1
if %tries% gtr 240 (echo failed: Jarvis didn't quit> "{result}" & goto start)
ping -n 2 127.0.0.1 >nul
goto wait
:install
"{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR="{root}"
set code=%errorlevel%
if "%code%"=="0" (echo updated> "{result}") else (echo failed: the installer stopped with code %code%> "{result}")
:start
start "" "{root}\Jarvis.exe"
del "%~f0"
"""
MAC_UPDATER = r"""#!/bin/sh
# Jarvis's macOS updater, written by src/app/updates.py: after Jarvis quits, move the new Jarvis.app into place
# and keep the old one until the next start; if a move fails, put the old one back. Then open Jarvis.
pid="$1"; app="$2"; new="$3"; backup="$4"; result="$5"; opener="$6"
i=0
while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 240 ]; then echo "failed: Jarvis didn't quit" > "$result"; exit 1; fi
    sleep 0.5
done
rm -rf "$backup"
mkdir -p "$(dirname "$backup")"
if mv "$app" "$backup"; then
    if mv "$new" "$app"; then
        echo updated > "$result"
    else
        mv "$backup" "$app"
        echo "failed: the new version couldn't be moved into place" > "$result"
    fi
else
    echo "failed: $app couldn't be moved" > "$result"
fi
"$opener" "$app"
rm -f "$0"
"""


def version_key(version: str) -> tuple:
    """"0.1.0" > "0.1.0-alpha.2" > "0.1.0-alpha.1" > "0.1.0.dev0": numbers first, then the release stage."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)(.*)$", str(version or ""))
    if not m:
        return ((0,), 0, 0)
    numbers = tuple(int(x) for x in m.group(1).split("."))
    numbers += (0,) * (3 - len(numbers))
    rest = m.group(2).lower().strip(".-")
    stage = next((rank for prefix, rank in (("rc", 3), ("beta", 2), ("alpha", 1), ("dev", 0)) if rest.startswith(prefix)),
                 4 if not rest else 0)
    extra = int((re.findall(r"(\d+)", rest) or ["0"])[-1])
    return numbers, stage, extra


def check(url: str, current: str = __version__, opener=urllib.request.urlopen) -> dict:
    """{"available", "version", "notes", "download"} or {"available": False, "error"}."""
    if not url:
        return {"available": False, "error": "The update address isn't set up yet."}
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": f"Jarvis/{current}"}), timeout=20) as r:
            latest = json.load(r)
    except Exception as e:
        return {"available": False, "error": f"Couldn't check for updates: {e}"}
    version = str(latest.get("version") or "")
    asset = latest.get(PLATFORM_KEY) or {}
    newer = bool(version) and version_key(version) > version_key(current)
    return {"available": newer and bool(asset.get("url")), "version": version, "notes": latest.get("notes", ""),
            "download": dict(asset, version=version) if asset else {}}


def download(asset: dict, progress: Optional[Callable[[int, int], None]] = None,
             opener=urllib.request.urlopen) -> Path:
    """Fetch the release file (the Windows installer, or the Mac's disk image) into cache/updates and check its
    SHA-256 (a mismatch deletes it)."""
    folder = paths.cache_dir() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / Path(asset["url"].split("?")[0]).name
    part = dest.with_name(dest.name + ".part")
    h, got = hashlib.sha256(), 0
    with opener(urllib.request.Request(asset["url"], headers={"User-Agent": f"Jarvis/{__version__}"}), timeout=60) as r, \
            open(part, "wb") as out:
        for chunk in iter(lambda: r.read(1 << 16), b""):
            out.write(chunk)
            h.update(chunk)
            got += len(chunk)
            if progress:
                progress(got, int(asset.get("size") or 0))
    if h.hexdigest() != str(asset.get("sha256", "")).lower():
        part.unlink(missing_ok=True)
        raise ValueError("The update didn't match its checksum, so it was deleted.")
    part.replace(dest)
    return dest


def _writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def updater_available() -> bool:
    """True in the installed Windows copy or Jarvis.app when Jarvis can replace itself (not when run from source, nor
    from a read-only place such as the disk image or the copy macOS runs straight from Downloads)."""
    root = paths.install_root()
    if not root:
        return False
    if IS_MAC:
        return "/AppTranslocation/" not in str(root) and _writable(root.parent)
    return (root / "Jarvis.exe").is_file() and _writable(root)


def staging_dir() -> Path:
    """Where the next version waits: the cache folder's updates/ (the Windows installer, where download() puts it)
    or update/ (the Mac's new Jarvis.app, copied out of its disk image)."""
    return paths.cache_dir() / ("update" if IS_MAC else "updates")


def backup_dir() -> Path:
    """macOS: the previous Jarvis.app, kept until the next start."""
    return paths.cache_dir() / "update-old" / "Jarvis.app"


def _pending() -> Optional[dict]:
    """Windows: the downloaded installer waiting for a restart, {"version", "path"}, or None."""
    try:
        note = json.loads((staging_dir() / PENDING).read_text(encoding="utf-8"))
        setup = staging_dir() / Path(note["file"]).name
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {"version": str(note.get("version") or ""), "path": setup} if setup.is_file() else None


def _copy_from_image(image: Path, staging: Path) -> None:
    """macOS: copy Jarvis.app out of the release's disk image, attached read-only where Finder doesn't show it."""
    from src.core import winproc
    mount = Path(tempfile.mkdtemp(prefix="jarvis-update-"))
    winproc.run_hidden(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount),
                        str(image)], timeout=300, check=True, capture_output=True)
    try:
        winproc.run_hidden(["/usr/bin/ditto", str(mount / "Jarvis.app"), str(staging / "Jarvis.app")], timeout=600,
                           check=True, capture_output=True)
    finally:
        detach = ["/usr/bin/hdiutil", "detach", str(mount)]
        if winproc.run_hidden(detach, timeout=120, capture_output=True).returncode != 0:
            winproc.run_hidden(detach + ["-force"], timeout=120, capture_output=True)
        try:
            mount.rmdir()
        except OSError:
            pass


def stage(release: Path, root: Optional[Path] = None, version: str = "") -> Path:
    """Get a checked download ready for the next restart, and return what restarting installs: on Windows the
    installer itself, noted with its version in pending.json; on macOS the new Jarvis.app, copied out of the disk
    image."""
    root = root or paths.install_root()
    if root is None:
        raise RuntimeError("Updates install only into an installed Jarvis.")
    if not IS_MAC:
        with open(release, "rb") as f:
            program = f.read(2) == b"MZ"
        if not program:
            release.unlink(missing_ok=True)
            raise ValueError("The update isn't a Windows program, so it was deleted.")
        if not version:
            m = re.search(r"Jarvis-Setup-(.+)-windows", release.name)
            version = m.group(1) if m else ""
        (release.parent / PENDING).write_text(json.dumps({"version": version, "file": release.name}), encoding="utf-8")
        return release
    staging = staging_dir()
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    _copy_from_image(release, staging)
    new = staging / "Jarvis.app"
    missing = [r for r in MAC_REQUIRED if not (new / r).is_file()]
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"The update is incomplete (no {missing[0]}).")
    return new


def prepare(asset: dict, progress: Optional[Callable[[int, int], None]] = None,
            opener=urllib.request.urlopen) -> Path:
    """Download, check and get an update ready so that restarting installs it. The Mac's disk image is deleted once
    the app is copied out of it; the Windows installer stays until it has run."""
    release = download(asset, progress, opener)
    if not IS_MAC:
        return stage(release, version=str(asset.get("version") or ""))
    try:
        return stage(release)
    finally:
        release.unlink(missing_ok=True)


def ready() -> bool:
    if not paths.install_root():
        return False
    if IS_MAC:
        return all((staging_dir() / "Jarvis.app" / r).is_file() for r in MAC_REQUIRED)
    return _pending() is not None


def staged_version() -> Optional[str]:
    """The version downloaded earlier and not installed yet, if it is newer than this one."""
    if not ready():
        return None
    if IS_MAC:
        try:
            text = (staging_dir() / "Jarvis.app" / "Contents/Resources/app/src/version.py").read_text(encoding="utf-8")
            version = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
        except (OSError, AttributeError):
            return None
    else:
        version = _pending()["version"]
    return version if version_key(version) > version_key(__version__) else None


def result_file() -> Path:
    return paths.home() / "update-result.txt"


def launch_updater(pid: Optional[int] = None) -> None:
    """Start the script that installs the waiting update once this Jarvis has quit; the caller quits right after."""
    root = paths.install_root()
    if root is None or not ready():
        raise RuntimeError("No update is ready to install.")
    from src.core import winproc
    result_file().unlink(missing_ok=True)
    if IS_MAC:
        script = Path(tempfile.gettempdir()) / f"Jarvis-Updater-{os.getpid()}.sh"
        script.write_text(MAC_UPDATER, encoding="utf-8")
        winproc.popen_hidden(["/bin/sh", str(script), str(pid or os.getpid()), str(root),
                              str(staging_dir() / "Jarvis.app"), str(backup_dir()), str(result_file()),
                              "/usr/bin/open"])
        return
    script = Path(tempfile.gettempdir()) / f"Jarvis-Updater-{os.getpid()}.cmd"
    text = WINDOWS_UPDATER.format(pid=pid or os.getpid(), setup=_pending()["path"], root=root, result=result_file())
    script.write_text(text.replace("\n", "\r\n"), encoding="utf-8", newline="")
    winproc.popen_hidden(["cmd", "/d", "/c", str(script)])


def after_start() -> Optional[str]:
    """At start: delete what the last update left behind, and return the updater's result once ("updated" or
    "failed: why"), or None when there was no update. A newer version downloaded earlier but not installed yet is
    kept, so it can be offered again without downloading it twice."""
    if paths.install_root():
        if IS_MAC:
            shutil.rmtree(backup_dir().parent, ignore_errors=True)
        if not staged_version():
            shutil.rmtree(staging_dir(), ignore_errors=True)
    for old in Path(tempfile.gettempdir()).glob("Jarvis-Updater-*.sh" if IS_MAC else "Jarvis-Updater-*.cmd"):
        try:
            old.unlink()
        except OSError:
            pass                                # still running, or another user's
    rf = result_file()
    if not rf.exists():
        return None
    try:
        return rf.read_text(encoding="utf-8-sig").strip() or None
    finally:
        rf.unlink(missing_ok=True)
