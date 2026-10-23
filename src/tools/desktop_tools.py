"""Desktop control for Jarvis: see and operate windows the way a person does.

- desktop_snapshot reads a window's UI Automation tree (buttons, links, fields, text) in one
  cached call and numbers the elements; desktop_click / desktop_type act on those numbers.
  Chrome exposes web pages to UI Automation, so this works inside the user's real Chrome
  profiles (with their logins) on pages opened by open_chrome, and in any other app.
- desktop_screenshot returns an image of a window for Claude to look at; desktop_click x/y
  are pixels in the latest screenshot.
- Input is real mouse and keyboard (SendInput). Jarvis's own windows (its approval dialog),
  consoles and desktop.blocked_processes are refused, typing is refused in
  desktop.no_typing_processes (Start menu, Explorer's address bar) and Win-key shortcuts are
  not sent, so desktop control cannot get around the approval and command policies.
  (Command Prompts Jarvis opens are typed into through terminal_tools, under the command policy.)
"""
import base64
import ctypes
import io
import os
import threading
import time
from ctypes import wintypes

from src.tools.registry import ToolResult, ToolSpec

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

BLOCKED_PROCESSES = ["cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe",
                     "openconsole.exe", "wsl.exe", "bash.exe", "regedit.exe", "mmc.exe", "taskmgr.exe",
                     "consent.exe", "credentialuibroker.exe", "lockapp.exe"]
CONSOLE_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe", "openconsole.exe"}
NO_TYPING_PROCESSES = ["explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe",
                       "shellexperiencehost.exe", "searchapp.exe"]

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


# ------------------------------------------------------------------ the tools

class Desktop:
    """Shared state between calls: the last snapshot's numbered elements and last screenshot geometry."""

    def __init__(self, config: dict):
        cfg = config.get("desktop", {}) or {}
        self.config = config
        self.blocked = {p.lower() for p in cfg.get("blocked_processes", BLOCKED_PROCESSES)}
        self.no_typing = {p.lower() for p in cfg.get("no_typing_processes", NO_TYPING_PROCESSES)}
        self.max_side = int(cfg.get("screenshot_max_side", 1400))
        self.own_pid = os.getpid()
        self.elements = {}            # id -> {name, type, rect, hwnd}
        self.shot = None              # {hwnd, left, top, scale}
        self.last_hwnd = 0            # the window Jarvis last looked at or acted in
        self.lock = threading.Lock()

    # -- window selection and safety

    def refusal(self, hwnd: int, typing: bool = False) -> str:
        pid, proc = process_name(hwnd)
        if pid == self.own_pid:
            return "That is a Jarvis window. Jarvis does not operate its own windows (approvals stay with the user)."
        if proc in self.blocked:
            hint = (" For a Command Prompt, use open_terminal, then terminal_type / terminal_read."
                    if proc in CONSOLE_PROCESSES else "")
            return f"Desktop control is not allowed in {proc} (desktop.blocked_processes).{hint}"
        if typing and proc in self.no_typing:
            return f"Typing into {proc} is not allowed (desktop.no_typing_processes). Use the dedicated tools instead."
        return ""

    def pick_window(self, query: str) -> tuple:
        """(window dict, error). Empty query = the frontmost window that is not Jarvis."""
        wins = [w for w in top_windows() if w["pid"] != self.own_pid]
        if not wins:
            return None, "No windows are open."
        q = (query or "").strip().lower()
        if not q:
            return wins[0], ""
        for test in (lambda w: w["title"].lower() == q, lambda w: q in w["title"].lower(),
                     lambda w: w["process"] in (q, q + ".exe")):
            hits = [w for w in wins if test(w)]
            if hits:
                return hits[0], ""
        return None, f"No window matches '{query}'. Open windows: " + "; ".join(
            f"{w['title'][:60]} ({w['process']})" for w in wins[:15])

    def prepare(self, query: str, typing: bool = False) -> tuple:
        """(window, error): the window named by query, else the one Jarvis last worked in, else
        the frontmost non-Jarvis window; checked against the blocklists and brought to the front."""
        if not query and self.last_hwnd and user32.IsWindow(self.last_hwnd):
            pid, proc = process_name(self.last_hwnd)
            w = {"hwnd": self.last_hwnd, "title": window_title(self.last_hwnd), "process": proc, "pid": pid}
        else:
            w, err = self.pick_window(query)
            if w is None:
                return None, err
        err = self.refusal(w["hwnd"], typing)
        if err:
            return None, err
        focus_window(w["hwnd"])        # some windows refuse focus but still accept input
        self.last_hwnd = w["hwnd"]
        return w, ""

    # -- reading

    def snapshot(self, hwnd: int, query: str = "", include_text: bool = True, limit: int = 150) -> list:
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

    def screenshot(self, hwnd: int) -> dict:
        from PIL import ImageGrab
        l, t, r, b = window_rect(hwnd)
        sl, st = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)      # virtual screen origin
        sw, sh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        l, t, r, b = max(l, sl), max(t, st), min(r, sl + sw), min(b, st + sh)
        img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True).convert("RGB")
        scale = min(1.0, self.max_side / max(img.width, img.height))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72)
        self.shot = {"hwnd": hwnd, "left": l, "top": t, "scale": scale}
        return {"data": base64.b64encode(buf.getvalue()).decode("ascii"), "mimeType": "image/jpeg",
                "width": img.width, "height": img.height}


def _line(i: int, el: dict) -> str:
    s = f"[{i}] {el['type']} \"{el['name']}\""
    if el["value"]:
        s += f" = \"{el['value']}\""
    if el["password"]:
        s += " (password field)"
    if not el["enabled"]:
        s += " (disabled)"
    if el["focused"]:
        s += " (focused)"
    return s


def register_desktop_tools(reg, config: dict) -> Desktop:
    if not (config.get("desktop", {}) or {}).get("enabled", True):
        return None
    import uiautomation as auto
    d = Desktop(config)

    def com(fn):
        def run(a, c):
            with auto.UIAutomationInitializerInThread():
                return fn(a, c)
        return run

    def dry(what: str):
        return ToolResult(ok=True, status="dry_run", dry_run=True, content=f"Would {what}") \
            if config.get("dry_run") else None

    def after(a):
        time.sleep(min(int(a.get("wait_ms", 400)), 5000) / 1000.0)

    def desktop_windows(a, c):
        wins = [w for w in top_windows() if w["pid"] != d.own_pid]
        fg = user32.GetForegroundWindow()
        lines = [f"- {w['title'][:100]} ({w['process']})" + (" — in front" if w["hwnd"] == fg else "") for w in wins]
        return ToolResult(ok=True, content="Open windows, front to back:\n" + "\n".join(lines),
                          data={"windows": [{k: w[k] for k in ("title", "process")} for w in wins]})

    def desktop_snapshot(a, c):
        w, err = d.pick_window(a.get("window", ""))
        if w is None:
            return ToolResult(ok=False, status="failed", content=err)
        err = d.refusal(w["hwnd"])
        if err:
            return ToolResult(ok=False, status="denied", content=err)
        d.last_hwnd = w["hwnd"]
        if a.get("focus", True):
            focus_window(w["hwnd"])
            time.sleep(0.15)
        els = d.snapshot(w["hwnd"], a.get("query", ""), a.get("include_text", True), int(a.get("max_elements", 150)))
        with d.lock:
            d.elements = {i: el for i, el in enumerate(els, 1)}
        lines = [f"Window: {w['title']} ({w['process']})",
                 f"{len(els)} visible elements" + (f" matching '{a['query']}'" if a.get("query") else "") +
                 ". Act on them with desktop_click / desktop_type by number:"]
        lines += [_line(i, el) for i, el in d.elements.items()]
        if not els:
            lines.append("(Nothing readable. Try desktop_screenshot, scroll, or a different query.)")
        res = ToolResult(ok=True, untrusted=True, content="\n".join(lines), data={"window": w["title"]})
        if a.get("screenshot"):
            res.data["images"] = [d.screenshot(w["hwnd"])]
        return res

    def desktop_screenshot(a, c):
        w, err = d.pick_window(a.get("window", ""))
        if w is None:
            return ToolResult(ok=False, status="failed", content=err)
        err = d.refusal(w["hwnd"])
        if err:
            return ToolResult(ok=False, status="denied", content=err)
        d.last_hwnd = w["hwnd"]
        focus_window(w["hwnd"])
        time.sleep(0.2)
        img = d.screenshot(w["hwnd"])
        return ToolResult(ok=True, content=f"Screenshot of {w['title']} ({w['process']}), {img['width']}x{img['height']} "
                                           "pixels. desktop_click x/y use these pixels.",
                          data={"images": [img], "window": w["title"]})

    def target_point(a) -> tuple:
        """(x, y, hwnd, label, error) on the real screen."""
        if a.get("element") is not None:
            with d.lock:
                el = d.elements.get(int(a["element"]))
            if el is None:
                return None, None, 0, "", f"No element {a['element']} in the latest snapshot. Take a new desktop_snapshot."
            l, t, r, b = el["rect"]
            return (l + r) // 2, (t + b) // 2, el["hwnd"], f"{el['type']} \"{el['name']}\"", ""
        if a.get("x") is not None and a.get("y") is not None:
            if not d.shot:
                return None, None, 0, "", "x/y need a desktop_screenshot first (they are pixels in that screenshot)."
            s = d.shot
            return (int(s["left"] + a["x"] / s["scale"]), int(s["top"] + a["y"] / s["scale"]), s["hwnd"],
                    f"point ({a['x']}, {a['y']})", "")
        return None, None, 0, "", "Give element (a number from desktop_snapshot) or x and y (screenshot pixels)."

    def checked_target(a, typing=False):
        x, y, hwnd, label, err = target_point(a)
        if err:
            return None, ToolResult(ok=False, status="invalid", content=err)
        err = d.refusal(hwnd, typing)
        if err:
            return None, ToolResult(ok=False, status="denied", content=err)
        focus_window(hwnd)
        d.last_hwnd = hwnd
        time.sleep(0.1)
        under = root_at(x, y)
        if under and under != hwnd:
            err = d.refusal(under, typing) or (
                f"Another window ({window_title(under)[:60]}) covers that spot. Take a new snapshot.")
            return None, ToolResult(ok=False, status="failed", content=err)
        return (x, y, hwnd, label), None

    def desktop_click(a, c):
        tgt, bad = checked_target(a)
        if bad:
            return bad
        x, y, hwnd, label = tgt
        what = f"{'double-' if a.get('double') else ''}{a.get('button', 'left')}-click {label} in {window_title(hwnd)[:60]}"
        r = dry(what)
        if r:
            return r
        click_at(x, y, a.get("button", "left"), bool(a.get("double")))
        after(a)
        return ToolResult(ok=True, content=f"Clicked {label}. Take a new desktop_snapshot to see the result.")

    def desktop_type(a, c):
        if a.get("element") is not None or a.get("x") is not None:
            tgt, bad = checked_target(a, typing=True)
            if bad:
                return bad
            x, y, hwnd, label = tgt
        else:
            w, err = d.prepare(a.get("window", ""), typing=True)
            if w is None:
                return ToolResult(ok=False, status="denied" if "allowed" in err or "Jarvis" in err else "failed",
                                  content=err)
            hwnd, x, y = w["hwnd"], None, None
            label = f"the focused field in {w['title'][:60]}"
            time.sleep(0.1)
        r = dry(f"type {len(a['text'])} characters into {label}")
        if r:
            return r
        if x is not None:
            click_at(x, y)
            time.sleep(0.15)
        if a.get("clear"):
            press_combo([0x11, ord("A")])
            press_combo([0x2E])
        if user32.GetForegroundWindow() != hwnd or d.refusal(user32.GetForegroundWindow(), typing=True):
            return ToolResult(ok=False, status="failed", content="The target window lost focus; nothing was typed.")
        type_unicode(a["text"])
        if a.get("submit"):
            press_combo([0x0D])
        after(a)
        return ToolResult(ok=True, content=f"Typed into {label}" + (" and pressed Enter." if a.get("submit") else "."))

    def desktop_keys(a, c):
        try:
            vks = parse_keys(a["keys"])
        except ValueError as e:
            return ToolResult(ok=False, status="invalid", content=str(e))
        w, err = d.prepare(a.get("window", ""), typing=True)
        if w is None:
            return ToolResult(ok=False, status="denied" if "allowed" in err or "Jarvis" in err else "failed",
                              content=err)
        time.sleep(0.1)
        hwnd = user32.GetForegroundWindow()
        err = d.refusal(hwnd, typing=True)
        if err:
            return ToolResult(ok=False, status="denied", content=err)
        r = dry(f"press {a['keys']} in {window_title(hwnd)[:60]}")
        if r:
            return r
        for _ in range(int(a.get("times", 1))):
            press_combo(vks)
            time.sleep(0.05)
        after(a)
        return ToolResult(ok=True, content=f"Pressed {a['keys']} in {window_title(hwnd)[:60]}.")

    def desktop_scroll(a, c):
        if a.get("element") is not None or a.get("x") is not None:
            tgt, bad = checked_target(a)
            if bad:
                return bad
            x, y, hwnd, _ = tgt
        else:
            w, err = d.prepare(a.get("window", ""))
            if w is None:
                return ToolResult(ok=False, status="failed", content=err)
            l, t, r_, b = window_rect(w["hwnd"])
            x, y, hwnd = (l + r_) // 2, (t + b) // 2, w["hwnd"]
        amount = int(a.get("amount", 5))
        r = dry(f"scroll {a['direction']} {amount} in {window_title(hwnd)[:60]}")
        if r:
            return r
        scroll_at(x, y, amount if a["direction"] == "up" else -amount)
        after(a)
        return ToolResult(ok=True, content=f"Scrolled {a['direction']} by {amount}.")

    def desktop_focus(a, c):
        w, err = d.prepare(a["window"])
        if w is None:
            return ToolResult(ok=False, status="failed", content=err)
        return ToolResult(ok=True, content=f"{w['title']} ({w['process']}) is in front.")

    window_arg = {"type": "string", "maxLength": 200,
                  "description": "Part of the window title or the process name (e.g. 'Google Calendar', 'chrome', "
                                 "'notepad'). Empty = the frontmost window that is not Jarvis."}
    target = {"element": {"type": "integer", "minimum": 1, "description": "Element number from desktop_snapshot"},
              "x": {"type": "number", "minimum": 0, "description": "Pixel x in the latest desktop_screenshot"},
              "y": {"type": "number", "minimum": 0},
              "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000,
                          "description": "Wait after acting (default 400 ms) so the page can react"}}
    common = dict(risk="sensitive", timeout_s=30)

    reg.register(ToolSpec(
        name="desktop_windows", description="List the open windows on the PC (title and process), front to back.",
        input_schema={"type": "object", "properties": {}}, handler=com(desktop_windows), category="read",
        purpose=lambda a: "List open windows"))
    reg.register(ToolSpec(
        name="desktop_snapshot",
        description="Read a window's visible controls and text through Windows UI Automation and number them, so "
                    "desktop_click / desktop_type can act on them. Works on web pages in the user's real Chrome "
                    "(after open_chrome) with their logins, and on any desktop app. query filters by text. "
                    "screenshot=true also returns an image. Take a new snapshot after every action that changes "
                    "the screen; numbers are only valid for the latest snapshot.",
        input_schema={"type": "object", "properties": {
            "window": window_arg,
            "query": {"type": "string", "maxLength": 200},
            "include_text": {"type": "boolean", "description": "Include plain text and images (default true)"},
            "max_elements": {"type": "integer", "minimum": 10, "maximum": 400},
            "screenshot": {"type": "boolean"},
            "focus": {"type": "boolean", "description": "Bring the window to the front first (default true)"}}},
        handler=com(desktop_snapshot), category="read", **common,
        purpose=lambda a: f"Look at {a.get('window') or 'the front window'}"))
    reg.register(ToolSpec(
        name="desktop_screenshot",
        description="Take a screenshot of a window and look at it. Use when desktop_snapshot does not show what you "
                    "need (canvas, images, custom widgets); desktop_click x/y are pixels in this image.",
        input_schema={"type": "object", "properties": {"window": window_arg}},
        handler=com(desktop_screenshot), category="read", **common,
        purpose=lambda a: f"Screenshot {a.get('window') or 'the front window'}"))
    reg.register(ToolSpec(
        name="desktop_click",
        description="Click with the real mouse on an element from the latest desktop_snapshot (element) or a point "
                    "in the latest desktop_screenshot (x, y). Before a click that sends, buys, deletes or publishes "
                    "something, make sure the user asked for exactly that.",
        input_schema={"type": "object", "properties": dict(target, **{
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "double": {"type": "boolean"}})},
        handler=com(desktop_click), **common,
        purpose=lambda a: f"Click {'element ' + str(a['element']) if a.get('element') else (a.get('x'), a.get('y'))}"))
    reg.register(ToolSpec(
        name="desktop_type",
        description="Type text with the real keyboard. With element (or x/y) it clicks that field first; otherwise "
                    "it types into the focused field of window (default: the window you last looked at or acted in). clear=true replaces the field's text; submit=true presses "
                    "Enter after.",
        input_schema={"type": "object", "properties": dict(target, **{
            "text": {"type": "string", "minLength": 1, "maxLength": 5000}, "window": window_arg,
            "clear": {"type": "boolean"}, "submit": {"type": "boolean"}}), "required": ["text"]},
        handler=com(desktop_type), **common,
        purpose=lambda a: f"Type “{a.get('text', '')[:60]}”"))
    reg.register(ToolSpec(
        name="desktop_keys",
        description="Press a key or shortcut in window (default: the window you last looked at or acted in): "
                    "'enter', 'tab', 'esc', 'ctrl+l', 'ctrl+shift+t', 'alt+left', 'pagedown', 'f5' ... "
                    "times repeats it. Windows-key shortcuts are not allowed.",
        input_schema={"type": "object", "properties": {
            "keys": {"type": "string", "minLength": 1, "maxLength": 60}, "window": window_arg,
            "times": {"type": "integer", "minimum": 1, "maximum": 30},
            "wait_ms": target["wait_ms"]}, "required": ["keys"]},
        handler=com(desktop_keys), **common, purpose=lambda a: f"Press {a.get('keys')}"))
    reg.register(ToolSpec(
        name="desktop_scroll",
        description="Scroll with the mouse wheel over an element, a screenshot point, or the middle of a window.",
        input_schema={"type": "object", "properties": dict(target, **{
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Wheel notches (default 5)"},
            "window": window_arg}), "required": ["direction"]},
        handler=com(desktop_scroll), **common, purpose=lambda a: f"Scroll {a.get('direction')}"))
    reg.register(ToolSpec(
        name="desktop_focus", description="Bring a window to the front.",
        input_schema={"type": "object", "properties": {"window": window_arg}, "required": ["window"]},
        handler=com(desktop_focus), **common, purpose=lambda a: f"Focus {a.get('window')}"))
    return d
