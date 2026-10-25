"""Common apps found in the Start Menu (Windows) or the Applications folders (macOS), so "open Spotify" or
"open VS Code" works without editing the profile.

Only a curated list of well-known apps is offered (COMMON), matched by shortcut or app name: every app becomes
part of the open_app tool's description, so offering everything installed would cost the model tokens on every
request. The user's own profile entries always win; apps.discover = false turns this off.

On Windows the shortcut (.lnk) itself is stored as the app's path and opened like Explorer opens it, which also
works for shortcuts without a plain target (Office's advertised shortcuts, for example). On macOS the app bundle
(.app) is stored and opened with `open -a`.
"""
import os
import sys
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
# App bundle names on macOS where they differ from the Start Menu's (the others are the same).
MAC_NAMES = {
    "chrome": ("Google Chrome",),
    "vs code": ("Visual Studio Code",),
    "word": ("Microsoft Word",),
    "excel": ("Microsoft Excel",),
    "powerpoint": ("Microsoft PowerPoint",),
    "outlook": ("Microsoft Outlook",),
    "onenote": ("Microsoft OneNote",),
    "teams": ("Microsoft Teams", "Microsoft Teams (work or school)", "Microsoft Teams classic"),
    "zoom": ("zoom.us",),
    "vlc": ("VLC",),
    "obs": ("OBS",),
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


def application_dirs() -> list:
    """macOS: /Applications and ~/Applications (apps installed for this user only)."""
    return [d for d in (Path("/Applications"), Path.home() / "Applications") if d.is_dir()]


def app_dirs() -> list:
    return application_dirs() if sys.platform == "darwin" else start_menu_dirs()


def _kind() -> str:
    return "app" if sys.platform == "darwin" else "lnk"


def _folders(d: Path, kind: str) -> list:
    """The folders whose contents decide what is found: every folder below the Start Menu; an Applications folder
    and its subfolders that aren't apps (such as Utilities), never the inside of an app bundle."""
    if kind == "lnk":
        return [Path(root) for root, _, _ in os.walk(d)]
    return [d] + [p for p in Path(d).iterdir() if p.is_dir() and p.suffix != ".app"]


def _signature(dirs: list, kind: str) -> tuple:
    """Changes when shortcuts or apps are added or removed (folder modification times)."""
    sig = []
    for d in dirs:
        try:
            folders = _folders(Path(d), kind)
        except OSError:
            continue
        for folder in folders:
            try:
                sig.append((str(folder), os.stat(folder).st_mtime_ns))
            except OSError:
                pass
    return tuple(sorted(sig))


def scan(dirs: list, kind: Optional[str] = None) -> dict:
    """{name in lower case: path} for every shortcut (.lnk) below the folders, or every app (.app) in them and in
    their subfolders (first one wins)."""
    kind = kind or _kind()
    found = {}
    for d in dirs:
        if kind == "lnk":
            paths = sorted(Path(d).rglob("*.lnk"))
        else:
            paths = sorted(p for folder in _folders(Path(d), kind) for p in folder.glob("*.app"))
        for path in paths:
            stem = path.stem.strip()
            if any(word in stem.lower() for word in SKIP_WORDS):
                continue
            found.setdefault(stem.lower(), str(path))
    return found


def discover(dirs: Optional[list] = None, kind: Optional[str] = None) -> dict:
    """{app key: {"path", "aliases", "source"}} for the common apps installed on this computer. kind: "lnk" (Start
    Menu shortcuts) or "app" (macOS app bundles), by default this OS's."""
    kind = kind or _kind()
    dirs = app_dirs() if dirs is None else dirs
    sig = (kind, _signature(dirs, kind))
    if _cache.get("sig") == sig:
        return dict(_cache["apps"])
    shortcuts = scan(dirs, kind)
    apps = {}
    for key, (names, aliases) in COMMON.items():
        if kind == "app":
            names = MAC_NAMES.get(key, names)
        for name in names:
            if name.lower() in shortcuts:
                apps[key] = {"path": shortcuts[name.lower()], "aliases": list(aliases),
                             "source": "applications" if kind == "app" else "start_menu"}
                break
    _cache.update(sig=sig, apps=apps)
    return dict(apps)
