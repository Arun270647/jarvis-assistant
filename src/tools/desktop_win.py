"""Desktop control on Windows: the UI Automation tree, SendInput for the real mouse and keyboard, and window
lookup through user32. src/tools/desktop_tools.py holds the tools and the safety rules; this module only talks
to Windows (desktop_mac.py has the same functions for macOS).
"""
import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

UI_TREE = "Windows UI Automation"
KEYS_HELP = ("'enter', 'tab', 'esc', 'ctrl+l', 'ctrl+shift+t', 'alt+left', 'pagedown', 'f5' ... "
             "Windows-key shortcuts are not allowed.")

# UI Automation property and control type ids
P_RECT, P_TYPE, P_NAME, P_FOCUS, P_ENABLED, P_OFFSCREEN, P_PASSWORD, P_VALUE = \
    30001, 30003, 30005, 30008, 30010, 30022, 30019, 30045
CONTROL_TYPES = {50000: "button", 50002: "checkbox", 50003: "combobox", 50004: "edit", 50005: "link",
                 50006: "image", 50007: "listitem", 50011: "menuitem", 50012: "progressbar", 50013: "radio",
                 50015: "slider", 50016: "spinner", 50018: "tab", 50019: "tabitem", 50020: "text",
                 50024: "treeitem", 50029: "dataitem", 50031: "splitbutton", 50030: "document"}
ACTIONABLE = {50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50015, 50016, 50019, 50024, 50029, 50031}
TEXTUAL = {50020, 50006}

VK = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
      "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
      "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "menu": 0x5D, "apps": 0x5D}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27}


# ------------------------------------------------------------------ SendInput

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(inputs: list) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _key(vk: int = 0, up: bool = False, char: str = "") -> _INPUT:
    flags = (0x0002 if up else 0) | (0x0001 if vk in EXTENDED else 0)
    if char:
        return _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(0, ord(char), flags | 0x0004, 0, 0)))  # KEYEVENTF_UNICODE
    return _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(vk, 0, flags, 0, 0)))


def _mouse(flags: int, data: int = 0) -> _INPUT:
    return _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))


def type_unicode(text: str) -> None:
    """Type text as Unicode key packets. Down and up go as separate inputs with small gaps:
    sent together, a busy target sometimes loses the key-up and auto-repeats ("tttttt")."""
    for ch in text:
        if ch == "\n":
            pairs = [(_key(0x0D), _key(0x0D, up=True))]
        else:
            units = memoryview(ch.encode("utf-16-le")).cast("H")          # surrogate pairs for emoji
            pairs = [(_key(char=chr(u)), _key(char=chr(u), up=True)) for u in units]
        for down, up in pairs:
            _send([down])
            time.sleep(0.006)
            _send([up])
            time.sleep(0.01)


def parse_keys(combo: str) -> list:
    """'ctrl+shift+t' -> [vk, ...]. Raises ValueError for unknown or Win keys."""
    vks = []
    for part in [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]:
        if part in ("win", "windows", "lwin", "rwin", "super", "meta", "cmd"):
            raise ValueError("Windows-key shortcuts are not allowed")
        if part in VK:
            vks.append(VK[part])
        elif len(part) == 1 and (part.isalnum()):
            vks.append(ord(part.upper()))
        elif len(part) == 1:
            r = user32.VkKeyScanW(ord(part))
            if r == -1:
                raise ValueError(f"Unknown key: {part}")
            vks.append(r & 0xFF)
        else:
            raise ValueError(f"Unknown key: {part}")
    if not vks:
        raise ValueError("No keys given")
    return vks


def press_combo(vks: list) -> None:
    for v in vks:                     # one input at a time: batched, the modifier can arrive late
        _send([_key(v)])
        time.sleep(0.015)
    for v in reversed(vks):
        _send([_key(v, up=True)])
        time.sleep(0.015)


def click_at(x: int, y: int, button: str = "left", double: bool = False) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    down, up = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}[button]
    for _ in range(2 if double else 1):
        _send([_mouse(down), _mouse(up)])
        time.sleep(0.06)


def scroll_at(x: int, y: int, clicks: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    _send([_mouse(0x0800, 120 * clicks)])        # MOUSEEVENTF_WHEEL, positive = up


# ------------------------------------------------------------------ windows

def process_name(hwnd: int) -> tuple:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value)
        kernel32.CloseHandle(h)
    return pid.value, name.lower()


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_rect(hwnd: int) -> tuple:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _cloaked(hwnd: int) -> bool:
    val = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        return False
    return bool(val.value)


def top_windows() -> list:
    """Visible top-level windows with a title, front to back: [{hwnd, title, process, pid}]."""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not _cloaked(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 \
                and not (user32.GetWindowLongW(hwnd, -20) & 0x80):          # skip WS_EX_TOOLWINDOW
            pid, proc = process_name(hwnd)
            out.append({"hwnd": int(hwnd), "title": window_title(hwnd), "process": proc, "pid": pid})
        return True

    user32.EnumWindows(cb, 0)
    return out


def focus_window(hwnd: int) -> bool:
    """Bring a window to the front from a background process.

    A zero-distance synthetic mouse move makes this process the last input source, which lifts
    the foreground lock. (The usual ALT tap is not used: it opens the target's menu bar, and
    the text typed next turns into menu shortcuts.)
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    _send([_mouse(0x0001)])                                         # MOUSEEVENTF_MOVE by (0, 0)
    if fg_thread and fg_thread != me:
        user32.AttachThreadInput(me, fg_thread, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if fg_thread and fg_thread != me:
        user32.AttachThreadInput(me, fg_thread, False)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.025)
    else:
        return False
    # Input sent the moment a window becomes foreground is sometimes dropped or garbled, so
    # wait until its thread has a focused control, then a little longer for it to settle.
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
    for _ in range(20):
        if user32.GetGUIThreadInfo(target_thread, ctypes.byref(info)) and info.hwndFocus:
            break
        time.sleep(0.025)
    time.sleep(0.2)
    return True


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def root_at(x: int, y: int) -> int:
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return int(user32.GetAncestor(hwnd, 2) or 0) if hwnd else 0          # GA_ROOT


# ------------------------------------------------------------------ reading

def read_elements(hwnd: int, query: str = "", include_text: bool = True, limit: int = 150) -> list:
    """The window's visible controls and text, from one cached UI Automation call."""
    import uiautomation.uiautomation as U
    cl = U._AutomationClient.instance()
    ia = cl.IUIAutomation
    cr = ia.CreateCacheRequest()
    for p in (P_RECT, P_TYPE, P_NAME, P_FOCUS, P_ENABLED, P_OFFSCREEN, P_PASSWORD, P_VALUE):
        cr.AddProperty(p)
    root = ia.ElementFromHandle(hwnd)
    arr = root.FindAllBuildCache(4, ia.ControlViewCondition, cr)       # TreeScope_Descendants
    wl, wt, wr, wb = window_rect(hwnd)
    q = (query or "").lower().strip()
    out, seen_text = [], set()
    for i in range(arr.Length):
        try:
            e = arr.GetElement(i)
            ct = e.CachedControlType
            if ct not in ACTIONABLE and not (include_text and ct in TEXTUAL):
                continue
            r = e.CachedBoundingRectangle
            if e.CachedIsOffscreen or r.right - r.left < 2 or r.bottom - r.top < 2:
                continue
            if r.right < wl or r.left > wr or r.bottom < wt or r.top > wb:
                continue
            name = " ".join((e.CachedName or "").split())
            value = ""
            if ct in (50004, 50003) and not e.CachedIsPassword:
                try:
                    value = " ".join(str(e.GetCachedPropertyValue(P_VALUE) or "").split())
                except Exception:
                    value = ""
            if ct in TEXTUAL:
                if not name or name in seen_text:
                    continue
                seen_text.add(name)
            if q and q not in name.lower() and q not in value.lower():
                continue
            if ct not in TEXTUAL and not name and not value and ct not in (50004,):
                continue
            rect = (r.left, r.top, r.right, r.bottom)
            if any(o["name"] == name[:160] and o["rect"][0] <= rect[0] and o["rect"][1] <= rect[1]
                   and o["rect"][2] >= rect[2] and o["rect"][3] >= rect[3] for o in out[-6:]):
                continue                    # the same item nested inside itself (web list rows)
            out.append({"name": name[:160], "type": CONTROL_TYPES.get(ct, str(ct)), "value": value[:160],
                        "rect": (r.left, r.top, r.right, r.bottom), "hwnd": hwnd,
                        "enabled": bool(e.CachedIsEnabled), "focused": bool(e.CachedHasKeyboardFocus),
                        "password": bool(e.CachedIsPassword)})
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def screen_box(hwnd: int) -> tuple:
    """The window's rectangle on screen, clipped to the virtual screen (all monitors)."""
    l, t, r, b = window_rect(hwnd)
    sl, st = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)      # virtual screen origin
    sw, sh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    return max(l, sl), max(t, st), min(r, sl + sw), min(b, st + sh)


def thread_scope():
    """UI Automation is COM: each worker thread initializes it before reading the tree."""
    import uiautomation as auto
    return auto.UIAutomationInitializerInThread()


def missing_permission(screen: bool = False) -> str:
    """Windows asks for no permission for desktop control."""
    return ""


SELECT_ALL, DELETE, ENTER = [0x11, ord("A")], [0x2E], [0x0D]
