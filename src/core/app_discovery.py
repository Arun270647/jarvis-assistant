"""Common apps found in the Start Menu, so "open Spotify" or "open VS Code" works without editing the profile.

Only a curated list of well-known apps is offered (COMMON), matched by shortcut name: every app becomes part
of the open_app tool's description, so offering all of the Start Menu would cost the model tokens on every
request. The user's own profile entries always win; apps.discover = false turns this off.

The shortcut (.lnk) itself is stored as the app's path and opened like Explorer opens it, which also works
for shortcuts without a plain target (Office's advertised shortcuts, for example).
"""
import os
from pathlib import Path
from typing import Optional

COMMON = {
    "chrome": (("Google Chrome",), ("browser", "google chrome", "web browser")),
    "edge": (("Microsoft Edge",), ("microsoft edge",)),
    "firefox": (("Firefox", "Mozilla Firefox"), ("mozilla firefox",)),
    "vs code": (("Visual Studio Code",), ("vscode", "code", "visual studio code")),
    "cursor": (("Cursor",), ("cursor editor",)),
    "spotify": (("Spotify",), ("music", "spotify music")),
    "word": (("Word", "Microsoft Word"), ("microsoft word",)),
    "excel": (("Excel", "Microsoft Excel"), ("microsoft excel", "spreadsheet")),
    "powerpoint": (("PowerPoint", "Microsoft PowerPoint"), ("microsoft powerpoint", "slides")),
    "outlook": (("Outlook", "Microsoft Outlook", "Outlook (classic)"), ("microsoft outlook",)),
    "onenote": (("OneNote",), ("microsoft onenote",)),
    "teams": (("Microsoft Teams", "Microsoft Teams (work or school)"), ("ms teams",)),
    "zoom": (("Zoom", "Zoom Workplace"), ("zoom meeting",)),
    "slack": (("Slack",), ()),
    "discord": (("Discord",), ()),
    "telegram": (("Telegram", "Telegram Desktop"), ()),
    "vlc": (("VLC media player",), ("vlc player", "media player")),
    "obs": (("OBS Studio", "OBS Studio (64bit)"), ("obs studio", "screen recorder")),
    "steam": (("Steam",), ()),
    "notepad++": (("Notepad++",), ("notepad plus plus",)),
    "postman": (("Postman",), ()),
    "android studio": (("Android Studio",), ()),
}
SKIP_WORDS = ("uninstall", "readme", "help", "website", "release notes", "documentation", "license")

_cache: dict = {}


def start_menu_dirs() -> list:
    dirs = []
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if base:
            d = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if d.is_dir():
                dirs.append(d)
    return dirs


def _signature(dirs: list) -> tuple:
    """Changes when shortcuts are added or removed (folder modification times)."""
    sig = []
    for d in dirs:
        for root, subdirs, _ in os.walk(d):
            try:
                sig.append((root, os.stat(root).st_mtime_ns))
            except OSError:
                pass
    return tuple(sorted(sig))


def scan(dirs: list) -> dict:
    """{shortcut name in lower case: path} for every .lnk below the folders (first one wins)."""
    found = {}
    for d in dirs:
        for path in sorted(Path(d).rglob("*.lnk")):
            stem = path.stem.strip()
            if any(word in stem.lower() for word in SKIP_WORDS):
                continue
            found.setdefault(stem.lower(), str(path))
    return found


def discover(dirs: Optional[list] = None) -> dict:
    """{app key: {"path", "aliases", "source"}} for the common apps installed on this PC."""
    dirs = start_menu_dirs() if dirs is None else dirs
    sig = _signature(dirs)
    if _cache.get("sig") == sig:
        return dict(_cache["apps"])
    shortcuts = scan(dirs)
    apps = {}
    for key, (names, aliases) in COMMON.items():
        for name in names:
            if name.lower() in shortcuts:
                apps[key] = {"path": shortcuts[name.lower()], "aliases": list(aliases), "source": "start_menu"}
                break
    _cache.update(sig=sig, apps=apps)
    return dict(apps)
