"""Updates: check the website's latest.json (updates.url; empty until the download site exists), then, in the
Windows zip or Jarvis.app, download, unpack and swap in the new version.

latest.json: {"version": "0.1.1", "notes": "...", "windows": {"url": ".../Jarvis-0.1.1-windows-x64.zip",
"sha256": "...", "size": 123}, "macos": {"url": ".../Jarvis-0.1.1-macos-arm64.dmg", ...}} (packaging/build_windows.py
and build_macos.py write it).

- check() says whether a newer version exists.
- prepare() downloads the zip into the cache folder, checks its SHA-256 and unpacks it into <Jarvis folder>\\.update
  (stage()), so it is ready before anyone is asked to restart.
- launch_updater() starts a copy of Jarvis-Updater.exe (packaging/windows/updater.c) from the temporary folder;
  Jarvis quits right after, the updater swaps the folders, keeps the old version in <Jarvis folder>\\.old and
  starts Jarvis again.
- after_start() deletes .old and .update and returns the updater's result for the app to show.

macOS: the release is a disk image. It is attached read-only and out of sight, and the new Jarvis.app is copied out
of it with ditto (which keeps its permissions, links and signature) into the cache folder's update/ (a folder inside
the app would break its signature); then the image is detached. A small shell script (MAC_UPDATER)
swaps the apps after Jarvis quits, keeping the old one in the cache folder's update-old/ until the next start.

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
import zipfile
from pathlib import Path
from typing import Callable, Optional

from src.core import paths
from src.version import __version__

IS_MAC = sys.platform == "darwin"
PLATFORM_KEY = "macos" if IS_MAC else "windows"
STAGING = ".update"                       # <Jarvis folder>\.update\Jarvis: the next version, unpacked
BACKUP = ".old"                           # <Jarvis folder>\.old: the previous version, until the next start
REQUIRED = ("Jarvis.exe", "Jarvis-Updater.exe", "runtime/pythonw.exe", "app/jarvis_gui.pyw")
MAC_REQUIRED = ("Contents/Info.plist", "Contents/MacOS/Jarvis", "Contents/Resources/runtime/bin/python3",
                "Contents/Resources/app/jarvis_gui.pyw")
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
            "download": asset}


def download(asset: dict, progress: Optional[Callable[[int, int], None]] = None,
             opener=urllib.request.urlopen) -> Path:
    """Fetch the release file (the zip, or the Mac's disk image) into cache/updates and check its SHA-256 (a mismatch
    deletes it)."""
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
    """True in the Windows zip or Jarvis.app when Jarvis can replace itself (not when run from source, nor from a
    read-only place such as the copy macOS runs straight from Downloads)."""
    root = paths.install_root()
    if not root:
        return False
    if IS_MAC:
        return "/AppTranslocation/" not in str(root) and _writable(root.parent)
    return (root / "Jarvis-Updater.exe").is_file() and _writable(root)


def staging_dir(root: Optional[Path] = None) -> Path:
    """Where the next version is unpacked: <Jarvis folder>\\.update, or the cache folder's update/ on macOS."""
    return paths.cache_dir() / "update" if IS_MAC else (root or paths.install_root()) / STAGING


def backup_dir(root: Optional[Path] = None) -> Path:
    return paths.cache_dir() / "update-old" / "Jarvis.app" if IS_MAC else (root or paths.install_root()) / BACKUP


def _new_version_root(staging: Path) -> Path:
    return staging / ("Jarvis.app" if IS_MAC else "Jarvis")


def _required() -> tuple:
    return MAC_REQUIRED if IS_MAC else REQUIRED


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


def stage(release: Path, root: Optional[Path] = None) -> Path:
    """Unpack a checked release (the zip, or the Mac's disk image) into the staging folder; returns the new Jarvis
    folder (or Jarvis.app) in it."""
    root = root or paths.install_root()
    if root is None:
        raise RuntimeError("Updates install only into the Jarvis folder from the zip.")
    staging = staging_dir(root)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    if IS_MAC:
        _copy_from_image(release, staging)
    else:
        with zipfile.ZipFile(release) as z:
            for name in z.namelist():
                target = (staging / name).resolve()
                if staging.resolve() not in target.parents:
                    raise ValueError(f"The update contains an unsafe path ({name}).")
            z.extractall(staging)
    new = _new_version_root(staging)
    missing = [r for r in _required() if not (new / r).is_file()]
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"The update is incomplete (no {missing[0]}).")
    return new


def prepare(asset: dict, progress: Optional[Callable[[int, int], None]] = None,
            opener=urllib.request.urlopen) -> Path:
    """Download, check and unpack an update so that restarting installs it; the download is deleted afterwards."""
    release = download(asset, progress, opener)
    try:
        return stage(release)
    finally:
        release.unlink(missing_ok=True)


def ready() -> bool:
    root = paths.install_root()
    return bool(root) and all((_new_version_root(staging_dir(root)) / r).is_file() for r in _required())


def staged_version() -> Optional[str]:
    """The version waiting in .update (downloaded earlier, not installed yet), if it is newer than this one."""
    root = paths.install_root()
    if not ready():
        return None
    app = _new_version_root(staging_dir(root)) / ("Contents/Resources/app" if IS_MAC else "app")
    try:
        text = (app / "src" / "version.py").read_text(encoding="utf-8")
        version = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    except (OSError, AttributeError):
        return None
    return version if version_key(version) > version_key(__version__) else None


def result_file() -> Path:
    return paths.home() / "update-result.txt"


def launch_updater(pid: Optional[int] = None) -> None:
    """Start the updater from a copy in the temporary folder (so it can replace its own file); quit right after."""
    root = paths.install_root()
    if root is None or not ready():
        raise RuntimeError("No update is ready to install.")
    from src.core import winproc
    if IS_MAC:
        script = Path(tempfile.gettempdir()) / f"Jarvis-Updater-{os.getpid()}.sh"
        script.write_text(MAC_UPDATER, encoding="utf-8")
        result_file().unlink(missing_ok=True)
        winproc.popen_hidden(["/bin/sh", str(script), str(pid or os.getpid()), str(root),
                              str(_new_version_root(staging_dir(root))), str(backup_dir(root)), str(result_file()),
                              "/usr/bin/open"])
        return
    copy = Path(tempfile.gettempdir()) / f"Jarvis-Updater-{os.getpid()}.exe"
    shutil.copy2(root / "Jarvis-Updater.exe", copy)
    result_file().unlink(missing_ok=True)
    winproc.popen_hidden([str(copy), "--install", str(root), "--pid", str(pid or os.getpid()),
                          "--result", str(result_file())])


def after_start() -> Optional[str]:
    """At start: delete what the last update left behind, and return the updater's result once ("updated" or
    "failed: why"), or None when there was no update. A newer version downloaded earlier but not installed yet
    stays in .update, so it can be offered again without downloading it twice."""
    root = paths.install_root()
    if root:
        shutil.rmtree(backup_dir(root).parent if IS_MAC else backup_dir(root), ignore_errors=True)
        if not staged_version():
            shutil.rmtree(staging_dir(root), ignore_errors=True)
    for old in Path(tempfile.gettempdir()).glob("Jarvis-Updater-*.sh" if IS_MAC else "Jarvis-Updater-*.exe"):
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
