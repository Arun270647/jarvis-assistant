"""Update check against the website's latest.json (updates.url; empty until the download site exists).

latest.json: {"version": "0.1.1", "notes": "...", "windows": {"url": ".../Jarvis-0.1.1-windows-x64.zip",
"sha256": "...", "size": 123}}. check() says whether a newer version exists; download() fetches the zip into
the cache folder and checks its SHA-256. Swapping the app folder needs Jarvis-Updater.exe from the packaged
release (Phase 6); until then the new version is offered as a download page.
"""
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from src.core import paths
from src.version import __version__

PLATFORM_KEY = "windows" if sys.platform == "win32" else "macos"


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


def updater_available() -> bool:
    return (paths.APP_ROOT / "Jarvis-Updater.exe").exists()
