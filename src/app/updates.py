"""Updates: check the website's latest.json (updates.url; empty until the download site exists), then, in the
Windows zip, download, unpack and swap in the new version.

latest.json: {"version": "0.1.1", "notes": "...", "windows": {"url": ".../Jarvis-0.1.1-windows-x64.zip",
"sha256": "...", "size": 123}} (packaging/build_windows.py writes it).

- check() says whether a newer version exists.
- prepare() downloads the zip into the cache folder, checks its SHA-256 and unpacks it into <Jarvis folder>\\.update
  (stage()), so it is ready before anyone is asked to restart.
- launch_updater() starts a copy of Jarvis-Updater.exe (packaging/windows/updater.c) from the temporary folder;
  Jarvis quits right after, the updater swaps the folders, keeps the old version in <Jarvis folder>\\.old and
  starts Jarvis again.
- after_start() deletes .old and .update and returns the updater's result for the app to show.

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

PLATFORM_KEY = "windows" if sys.platform == "win32" else "macos"
STAGING = ".update"                       # <Jarvis folder>\.update\Jarvis: the next version, unpacked
BACKUP = ".old"                           # <Jarvis folder>\.old: the previous version, until the next start
REQUIRED = ("Jarvis.exe", "Jarvis-Updater.exe", "runtime/pythonw.exe", "app/jarvis_gui.pyw")


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
    """Fetch the release zip into cache/updates and check its SHA-256 (a mismatch deletes it)."""
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
    """True in the Windows zip when Jarvis can replace its own folder (not when run from source)."""
    root = paths.install_root()
    return bool(root) and (root / "Jarvis-Updater.exe").is_file() and _writable(root)


def stage(zip_path: Path, root: Optional[Path] = None) -> Path:
    """Unpack a checked release zip into <Jarvis folder>\\.update; returns the new Jarvis folder inside it."""
    root = root or paths.install_root()
    if root is None:
        raise RuntimeError("Updates install only into the Jarvis folder from the zip.")
    staging = root / STAGING
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = (staging / name).resolve()
            if staging.resolve() not in target.parents:
                raise ValueError(f"The update contains an unsafe path ({name}).")
        z.extractall(staging)
    new = staging / "Jarvis"
    missing = [r for r in REQUIRED if not (new / r).is_file()]
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"The update is incomplete (no {missing[0]}).")
    return new


def prepare(asset: dict, progress: Optional[Callable[[int, int], None]] = None,
            opener=urllib.request.urlopen) -> Path:
    """Download, check and unpack an update so that restarting installs it; the zip is deleted afterwards."""
    zip_path = download(asset, progress, opener)
    try:
        return stage(zip_path)
    finally:
        zip_path.unlink(missing_ok=True)


def ready() -> bool:
    root = paths.install_root()
    return bool(root) and all((root / STAGING / "Jarvis" / r).is_file() for r in REQUIRED)


def staged_version() -> Optional[str]:
    """The version waiting in .update (downloaded earlier, not installed yet), if it is newer than this one."""
    root = paths.install_root()
    if not ready():
        return None
    try:
        text = (root / STAGING / "Jarvis" / "app" / "src" / "version.py").read_text(encoding="utf-8")
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
    copy = Path(tempfile.gettempdir()) / f"Jarvis-Updater-{os.getpid()}.exe"
    shutil.copy2(root / "Jarvis-Updater.exe", copy)
    result_file().unlink(missing_ok=True)
    from src.core import winproc
    winproc.popen_hidden([str(copy), "--install", str(root), "--pid", str(pid or os.getpid()),
                          "--result", str(result_file())])


def after_start() -> Optional[str]:
    """At start: delete what the last update left behind, and return the updater's result once ("updated" or
    "failed: why"), or None when there was no update. A newer version downloaded earlier but not installed yet
    stays in .update, so it can be offered again without downloading it twice."""
    root = paths.install_root()
    if root:
        shutil.rmtree(root / BACKUP, ignore_errors=True)
        if not staged_version():
            shutil.rmtree(root / STAGING, ignore_errors=True)
    for old in Path(tempfile.gettempdir()).glob("Jarvis-Updater-*.exe"):
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
