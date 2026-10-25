"""Desktop control on macOS: the Accessibility API (AXUIElement) for a window's controls, Quartz events for the real
mouse and keyboard, and the window server's window list (CGWindowList) for window lookup. src/tools/desktop_tools.py
holds the tools and the safety rules; this module has the same functions as desktop_win.py.

A window is named by its window-server number. Coordinates are points with the origin at the top left of the main
display, which is what the window list, the Accessibility API and Quartz mouse events all use. macOS asks the user
first: Accessibility for reading other apps and sending input, Screen Recording for screenshots (and other apps'
window titles, read through the Accessibility API instead when it is missing).

PyObjC is imported inside the functions, so this module loads on any OS (the key parser is tested everywhere).
"""
import contextlib
import time
from typing import Optional

UI_TREE = "the macOS Accessibility API"
KEYS_HELP = ("'enter', 'tab', 'esc', 'cmd+l', 'cmd+shift+t', 'cmd+left', 'pagedown', 'f5' ... (cmd is the Command "
             "key, option the Option key). Shortcuts that open a launcher or end the session (cmd+space, "
             "cmd+option+esc, cmd+shift+q) are not allowed.")

# Virtual key codes (Carbon's kVK_*): the US layout's positions, which shortcuts use on every layout.
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
    "delete": 117, "del": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}
CMD, SHIFT, OPTION, CONTROL = 55, 56, 58, 59
MODIFIER_NAMES = {"cmd": CMD, "command": CMD, "meta": CMD, "super": CMD, "win": CMD, "windows": CMD,
                  "shift": SHIFT, "alt": OPTION, "option": OPTION, "opt": OPTION, "ctrl": CONTROL, "control": CONTROL}
FLAGS = {SHIFT: 0x20000, CONTROL: 0x40000, OPTION: 0x80000, CMD: 0x100000}     # kCGEventFlagMask*
SPACE, ESC, Q = 49, 53, 12
# Shortcuts that open a launcher (Spotlight, input sources, Finder search) or force-quit / sign out.
BLOCKED_COMBOS = [({CMD, SPACE}, "cmd+space opens Spotlight"), ({CONTROL, SPACE}, "ctrl+space switches the input"),
                  ({CMD, OPTION, ESC}, "cmd+option+esc opens Force Quit"), ({CMD, SHIFT, Q}, "cmd+shift+q signs out"),
                  ({CONTROL, CMD, Q}, "ctrl+cmd+q locks the screen")]

SELECT_ALL, DELETE, ENTER = [CMD, KEYCODES["a"]], [KEYCODES["backspace"]], [KEYCODES["enter"]]

ROLES = {"AXButton": "button", "AXMenuButton": "button", "AXDisclosureTriangle": "button", "AXColorWell": "button",
         "AXCheckBox": "checkbox", "AXPopUpButton": "combobox", "AXComboBox": "combobox", "AXTextField": "edit",
         "AXTextArea": "edit", "AXLink": "link", "AXImage": "image", "AXRow": "listitem", "AXCell": "dataitem",
         "AXMenuItem": "menuitem", "AXProgressIndicator": "progressbar", "AXRadioButton": "radio",
         "AXSlider": "slider", "AXIncrementor": "spinner", "AXStaticText": "text", "AXHeading": "text"}
SUBROLES = {"AXTabButton": "tabitem", "AXOutlineRow": "treeitem", "AXSwitch": "checkbox"}
ACTIONABLE = {"button", "checkbox", "combobox", "edit", "link", "listitem", "dataitem", "menuitem", "radio",
              "slider", "spinner", "tabitem", "treeitem"}
TEXTUAL = {"text", "image"}
ATTRIBUTES = ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPosition", "AXSize", "AXEnabled",
              "AXFocused", "AXChildren"]
MAX_NODES, MAX_SECONDS = 6000, 4.0          # a huge web page is cut off rather than freezing the tool

_asked = set()                              # permissions macOS was asked for in this session
_manual_accessibility = set()               # apps (pids) asked to expose their full tree (Chrome, Electron)


# ------------------------------------------------------------------ keys

def parse_keys(combo: str) -> list:
    """'cmd+shift+t' -> [key code, ...] (modifiers first, as given). Raises ValueError for unknown keys and for the
    shortcuts in BLOCKED_COMBOS."""
    codes = []
    for part in [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]:
        if part in MODIFIER_NAMES:
            codes.append(MODIFIER_NAMES[part])
        elif part in KEYCODES:
            codes.append(KEYCODES[part])
        else:
            raise ValueError(f"Unknown key: {part}")
    if not codes:
        raise ValueError("No keys given")
    for keys, why in BLOCKED_COMBOS:
        if keys <= set(codes):
            raise ValueError(f"That shortcut is not allowed ({why})")
    return codes


def _post(event) -> None:
    import Quartz
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def press_combo(codes: list) -> None:
    import Quartz
    flags = 0
    for code in codes:                      # one at a time: batched, the modifier can arrive late
        flags |= FLAGS.get(code, 0)
        event = Quartz.CGEventCreateKeyboardEvent(None, code, True)
        Quartz.CGEventSetFlags(event, flags)
        _post(event)
        time.sleep(0.015)
    for code in reversed(codes):
        flags &= ~FLAGS.get(code, 0)
        event = Quartz.CGEventCreateKeyboardEvent(None, code, False)
        Quartz.CGEventSetFlags(event, flags)
        _post(event)
        time.sleep(0.015)


def type_unicode(text: str) -> None:
    """Type text as Unicode key events, one character at a time with small gaps (like the Windows version)."""
    import Quartz
    for ch in text:
        if ch == "\n":
            press_combo(ENTER)
            continue
        units = len(ch.encode("utf-16-le")) // 2           # 2 for emoji outside the basic plane
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventKeyboardSetUnicodeString(event, units, ch)
            _post(event)
            time.sleep(0.006 if down else 0.01)


# ------------------------------------------------------------------ mouse

def _move(x: int, y: int) -> tuple:
    import Quartz
    point = (float(x), float(y))
    _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft))
    time.sleep(0.03)
    return point


def click_at(x: int, y: int, button: str = "left", double: bool = False) -> None:
    import Quartz
    point = _move(x, y)
    down, up, which = {
        "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft),
        "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
        "middle": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
    }[button]
    for count in range(1, 3 if double else 2):
        for kind in (down, up):
            event = Quartz.CGEventCreateMouseEvent(None, kind, point, which)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, count)
            _post(event)
        time.sleep(0.06)


def scroll_at(x: int, y: int, clicks: int) -> None:
    import Quartz
    _move(x, y)
    _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, int(clicks)))  # + = up


# ------------------------------------------------------------------ windows

def _window_list(window: int = 0) -> list:
    """The window server's on-screen windows, front to back (or just `window`)."""
    import Quartz
    if window:
        return list(Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionIncludingWindow, window) or [])
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return list(Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or [])


def _info(window: int) -> Optional[dict]:
    infos = _window_list(window)
    return infos[0] if infos else None


def _bounds(info: dict) -> tuple:
    b = info.get("kCGWindowBounds") or {}
    left, top = int(b.get("X", 0)), int(b.get("Y", 0))
    return left, top, left + int(b.get("Width", 0)), top + int(b.get("Height", 0))


def _program(pid: int, fallback: str = "") -> str:
    """The app's program name in lower case ("google chrome", "terminal"), the name the block lists use."""
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        url = app.executableURL() if app is not None else None
        if url is not None:
            return str(url.lastPathComponent()).lower()
    except Exception:
        pass
    return fallback.lower()


def _ax(element, attribute: str):
    import ApplicationServices as AS
    err, value = AS.AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == 0 else None


def _ax_app(pid: int):
    import ApplicationServices as AS
    app = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(app, 1.5)
    if pid not in _manual_accessibility:
        _manual_accessibility.add(pid)
        AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)     # Chrome and Electron apps
    return app


def _decode(value):
    """AXValue structs (point, size) as tuples; AXValue errors (a missing attribute) as None."""
    import ApplicationServices as AS
    if value is None or type(value).__name__ != "AXValueRef":
        return value
    kind = AS.AXValueGetType(value)
    if kind == getattr(AS, "kAXValueCGPointType", getattr(AS, "kAXValueTypeCGPoint", 1)):
        ok, point = AS.AXValueGetValue(value, kind, None)
        return (point.x, point.y) if ok else None
    if kind == getattr(AS, "kAXValueCGSizeType", getattr(AS, "kAXValueTypeCGSize", 2)):
        ok, size = AS.AXValueGetValue(value, kind, None)
        return (size.width, size.height) if ok else None
    return None


def _frame(element) -> Optional[tuple]:
    pos, size = _decode(_ax(element, "AXPosition")), _decode(_ax(element, "AXSize"))
    if not pos or not size:
        return None
    return int(pos[0]), int(pos[1]), int(pos[0] + size[0]), int(pos[1] + size[1])


def _ax_window(window: int, info: Optional[dict] = None):
    """The Accessibility element of a window-server window: the app's window with the same frame (or title)."""
    info = info or _info(window)
    if not info:
        return None
    wins = list(_ax(_ax_app(int(info["kCGWindowOwnerPID"])), "AXWindows") or [])
    target = _bounds(info)
    for w in wins:
        frame = _frame(w)
        if frame and all(abs(a - b) <= 2 for a, b in zip(frame, target)):
            return w
    title = str(info.get("kCGWindowName") or "")
    for w in wins:
        if title and str(_ax(w, "AXTitle") or "") == title:
            return w
    return wins[0] if len(wins) == 1 else None


def _title(info: dict) -> str:
    title = str(info.get("kCGWindowName") or "")      # empty without Screen Recording: ask Accessibility instead
    if not title:
        try:
            w = _ax_window(int(info["kCGWindowNumber"]), info)
            title = str(_ax(w, "AXTitle") or "") if w is not None else ""
        except Exception:
            title = ""
    return title or str(info.get("kCGWindowOwnerName") or "")


def _is_app_window(info: dict) -> bool:
    x0, y0, x1, y1 = _bounds(info)
    return int(info.get("kCGWindowLayer", 1)) == 0 and float(info.get("kCGWindowAlpha", 1)) > 0.01 \
        and x1 - x0 > 40 and y1 - y0 > 40


def top_windows() -> list:
    """Visible app windows, front to back: [{hwnd, title, process, pid}] (hwnd is the window-server number)."""
    out = []
    for info in _window_list():
        if not _is_app_window(info):
            continue
        pid = int(info.get("kCGWindowOwnerPID", 0))
        out.append({"hwnd": int(info["kCGWindowNumber"]), "title": _title(info),
                    "process": _program(pid, str(info.get("kCGWindowOwnerName") or "")), "pid": pid})
    return out


def process_name(window: int) -> tuple:
    info = _info(window)
    if not info:
        return 0, ""
    pid = int(info.get("kCGWindowOwnerPID", 0))
    return pid, _program(pid, str(info.get("kCGWindowOwnerName") or ""))


def window_title(window: int) -> str:
    info = _info(window)
    return _title(info) if info else ""


def window_rect(window: int) -> tuple:
    info = _info(window)
    return _bounds(info) if info else (0, 0, 0, 0)


def foreground() -> int:
    """The front window of the frontmost app."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        pid = int(app.processIdentifier()) if app is not None else 0
    except Exception:
        return 0
    for info in _window_list():
        if int(info.get("kCGWindowOwnerPID", 0)) == pid and _is_app_window(info):
            return int(info["kCGWindowNumber"])
    return 0


def is_window(window: int) -> bool:
    info = _info(window)
    return bool(info) and bool(info.get("kCGWindowIsOnscreen", False))


def focus_window(window: int) -> bool:
    """Make the window's app frontmost and raise the window (through Accessibility, which works from the
    background), then wait until it is in front and settled, like the Windows version."""
    import ApplicationServices as AS
    from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
    info = _info(window)
    if not info:
        return False
    pid = int(info["kCGWindowOwnerPID"])
    try:
        AS.AXUIElementSetAttributeValue(_ax_app(pid), "AXFrontmost", True)
        w = _ax_window(window, info)
        if w is not None:
            if _ax(w, "AXMinimized"):
                AS.AXUIElementSetAttributeValue(w, "AXMinimized", False)
            AS.AXUIElementPerformAction(w, "AXRaise")
            AS.AXUIElementSetAttributeValue(w, "AXMain", True)
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None and not app.isActive():
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception as e:
        print(f"[DESKTOP] Could not bring window {window} to the front: {e}")
    for _ in range(20):
        if foreground() == window:
            break
        time.sleep(0.025)
    else:
        return False
    time.sleep(0.2)
    return True


def root_at(x: int, y: int) -> int:
    """The frontmost window under a point, including floating ones (Jarvis's orb) but not the menu bar."""
    for info in _window_list():
        layer = int(info.get("kCGWindowLayer", 0))
        if layer < 0 or layer >= 24 or float(info.get("kCGWindowAlpha", 1)) <= 0.01:
            continue
        if str(info.get("kCGWindowOwnerName") or "") == "Window Server":
            continue
        x0, y0, x1, y1 = _bounds(info)
        if x0 <= x < x1 and y0 <= y < y1:
            return int(info["kCGWindowNumber"])
    return 0


def screen_box(window: int) -> tuple:
    """The window's rectangle, clipped to the displays."""
    import Quartz
    l, t, r, b = window_rect(window)
    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err == 0 and count:
        rects = [Quartz.CGDisplayBounds(i) for i in ids[:count]]
        sl = min(int(d.origin.x) for d in rects)
        st = min(int(d.origin.y) for d in rects)
        sr = max(int(d.origin.x + d.size.width) for d in rects)
        sb = max(int(d.origin.y + d.size.height) for d in rects)
        l, t, r, b = max(l, sl), max(t, st), min(r, sr), min(b, sb)
    return l, t, r, b


# ------------------------------------------------------------------ reading

def read_elements(window: int, query: str = "", include_text: bool = True, limit: int = 150) -> list:
    """The window's visible controls and text, read from its Accessibility tree (breadth first)."""
    import ApplicationServices as AS
    root = _ax_window(window)
    if root is None:
        return []
    wl, wt, wr, wb = window_rect(window)
    q = (query or "").lower().strip()
    out, seen_text, queue, visited = [], set(), [root], 0
    deadline = time.monotonic() + MAX_SECONDS
    while queue and visited < MAX_NODES and time.monotonic() < deadline:
        element = queue.pop(0)
        visited += 1
        err, values = AS.AXUIElementCopyMultipleAttributeValues(element, ATTRIBUTES, 0, None)
        if err != 0 or not values:
            continue
        v = dict(zip(ATTRIBUTES, (_decode(x) for x in values)))
        queue.extend(v.get("AXChildren") or [])
        role, subrole = str(v.get("AXRole") or ""), str(v.get("AXSubrole") or "")
        kind = SUBROLES.get(subrole) or ROLES.get(role)
        if not kind or (kind not in ACTIONABLE and not (include_text and kind in TEXTUAL)):
            continue
        pos, size = v.get("AXPosition"), v.get("AXSize")
        if not pos or not size or size[0] < 2 or size[1] < 2:
            continue
        rect = (int(pos[0]), int(pos[1]), int(pos[0] + size[0]), int(pos[1] + size[1]))
        if rect[2] < wl or rect[0] > wr or rect[3] < wt or rect[1] > wb:
            continue
        password = subrole == "AXSecureTextField"
        raw_value = v.get("AXValue")
        text_value = raw_value if isinstance(raw_value, str) else ""
        name = " ".join(str(v.get("AXTitle") or v.get("AXDescription") or
                            (text_value if kind in TEXTUAL else "")).split())
        value = " ".join(text_value.split()) if kind in ("edit", "combobox") and not password else ""
        if kind in TEXTUAL:
            if not name or name in seen_text:
                continue
            seen_text.add(name)
        if q and q not in name.lower() and q not in value.lower():
            continue
        if kind not in TEXTUAL and not name and not value and kind != "edit":
            continue
        if any(o["name"] == name[:160] and o["rect"][0] <= rect[0] and o["rect"][1] <= rect[1]
               and o["rect"][2] >= rect[2] and o["rect"][3] >= rect[3] for o in out[-6:]):
            continue                        # the same item nested inside itself (web list rows)
        out.append({"name": name[:160], "type": kind, "value": value[:160], "rect": rect, "hwnd": window,
                    "enabled": v.get("AXEnabled") is not False, "focused": bool(v.get("AXFocused")),
                    "password": password})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ permissions and threads

def missing_permission(screen: bool = False) -> str:
    """A sentence for the user when macOS hasn't allowed desktop control (or screenshots) yet; empty when it has.
    The first time, macOS's own prompt is shown too."""
    from src.platform import current
    platform = current()
    needs = [("accessibility", "Accessibility")] + ([("screen", "Screen & System Audio Recording")] if screen else [])
    for kind, page in needs:
        if platform.permission(kind) == "granted":
            continue
        if kind not in _asked:
            _asked.add(kind)
            platform.request_permission(kind)
            if platform.permission(kind) == "granted":
                continue
        return (f"macOS hasn't allowed Jarvis to {'take screenshots' if kind == 'screen' else 'control other apps'} "
                f"yet. Ask the user to open System Settings › Privacy & Security › {page}, turn on Jarvis, then "
                "try again" + (" (Jarvis must be reopened after this one)." if kind == "screen" else "."))
    return ""


def thread_scope():
    """Each tool call runs on a worker thread: give the Objective-C objects it creates an autorelease pool."""
    try:
        import objc
        return objc.autorelease_pool()
    except ImportError:
        return contextlib.nullcontext()

