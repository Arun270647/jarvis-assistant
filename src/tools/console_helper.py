"""Reads and types into one console window. Run by terminal_tools as a short-lived hidden process.

Attaching to a console (AttachConsole) is process-wide and makes the process receive that
console's Ctrl+C, so it happens here and never inside Jarvis itself. Stand-alone on purpose:
stdlib only, no Jarvis imports.

Usage: console_helper.py '<json request>'  ->  one JSON line on stdout.
  {"op": "read", "pid": 123, "lines": 40}              screen text up to the cursor + running programs
  {"op": "type", "pid": 123, "text": "...", "clear": true, "submit": true}
  {"op": "key",  "pid": 123, "key": "ctrl+c"}
  {"op": "font", "pid": 123, "face": "Cascadia Mono"}    this window's font (keeps its size)
"""
import ctypes
import json
import sys
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

GENERIC_RW = 0x80000000 | 0x40000000
SHARE_RW = 0x1 | 0x2
OPEN_EXISTING = 3
KEY_EVENT = 0x0001
LEFT_CTRL_PRESSED = 0x0008
CONSOLE_HOSTS = {"conhost.exe", "openconsole.exe"}
KEYS = {"enter": (0x0D, "\r"), "esc": (0x1B, "\x1b"), "tab": (0x09, "\t"), "backspace": (0x08, "\b"),
        "up": (0x26, ""), "down": (0x28, ""), "left": (0x25, ""), "right": (0x27, "")}


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("UnicodeChar", wintypes.WCHAR),
                ("dwControlKeyState", wintypes.DWORD)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("pad", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class CSBI(ctypes.Structure):
    _fields_ = [("dwSize", wintypes._COORD), ("dwCursorPosition", wintypes._COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", wintypes._COORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class CONSOLE_FONT_INFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("nFont", wintypes.DWORD), ("dwFontSize", wintypes._COORD),
                ("FontFamily", wintypes.UINT), ("FontWeight", wintypes.UINT), ("FaceName", wintypes.WCHAR * 32)]


kernel32.CreateFileW.restype = wintypes.HANDLE


def descendants(pid: int) -> list:
    """Names of the programs running under pid (children first), without the console host."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
    procs = []
    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            procs.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower()))
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snap)
    out, frontier = [], {pid}
    while frontier:
        nxt = set()
        for p, parent, name in procs:
            if parent in frontier and p != pid and name not in CONSOLE_HOSTS:
                out.append(name)
                nxt.add(p)
        frontier = nxt
    return out


def attach(pid: int) -> None:
    kernel32.FreeConsole()
    kernel32.SetConsoleCtrlHandler(None, True)       # this helper ignores the console's Ctrl+C
    if not kernel32.AttachConsole(pid):
        raise OSError(f"cannot attach to the console of pid {pid} (error {ctypes.get_last_error()})")


def handle(name: str):
    h = kernel32.CreateFileW(name, GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(f"cannot open {name} (error {ctypes.get_last_error()})")
    return h


def read_screen(lines: int) -> dict:
    h = handle("CONOUT$")
    info = CSBI()
    kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(info))
    width, cy, cx = info.dwSize.X, info.dwCursorPosition.Y, info.dwCursorPosition.X
    buf = ctypes.create_unicode_buffer(width)
    n = wintypes.DWORD()
    text = []
    for y in range(max(0, cy - lines + 1), cy + 1):
        kernel32.ReadConsoleOutputCharacterW(h, buf, width, wintypes._COORD(0, y), ctypes.byref(n))
        text.append(buf.value[:n.value].rstrip())
    kernel32.ReadConsoleOutputCharacterW(h, buf, width, wintypes._COORD(0, cy), ctypes.byref(n))
    line = buf.value[:n.value]
    kernel32.CloseHandle(h)
    return {"lines": text, "before_cursor": line[:cx], "after_cursor": line[cx:].rstrip()}


def records(vk: int, char: str, ctrl: int = 0) -> list:
    scan = user32.MapVirtualKeyW(vk, 0) if vk else 0
    out = []
    for down in (True, False):
        r = INPUT_RECORD(EventType=KEY_EVENT)
        r.Event.KeyEvent = KEY_EVENT_RECORD(down, 1, vk, scan, char, ctrl)
        out.append(r)
    return out


def write_input(recs: list) -> None:
    h = handle("CONIN$")
    arr = (INPUT_RECORD * len(recs))(*recs)
    n = wintypes.DWORD()
    ok = kernel32.WriteConsoleInputW(h, arr, len(recs), ctypes.byref(n))
    kernel32.CloseHandle(h)
    if not ok or n.value != len(recs):
        raise OSError(f"writing console input failed (error {ctypes.get_last_error()})")


def set_font(face: str) -> None:
    """Switch this console window to another TrueType font, keeping its size (conhost has no glyph fallback)."""
    h = handle("CONOUT$")
    try:
        info = CONSOLE_FONT_INFOEX(cbSize=ctypes.sizeof(CONSOLE_FONT_INFOEX))
        if not kernel32.GetCurrentConsoleFontEx(h, False, ctypes.byref(info)):
            raise OSError(f"reading the console font failed (error {ctypes.get_last_error()})")
        info.FaceName = face
        info.FontFamily = 54                             # FF_MODERN | TMPF_TRUETYPE | TMPF_VECTOR
        info.dwFontSize = wintypes._COORD(0, info.dwFontSize.Y)
        if not kernel32.SetCurrentConsoleFontEx(h, False, ctypes.byref(info)):
            raise OSError(f"setting the console font failed (error {ctypes.get_last_error()})")
    finally:
        kernel32.CloseHandle(h)


def text_records(text: str) -> list:
    recs = []
    for ch in text:
        scan = user32.VkKeyScanW(ord(ch))
        recs += records(scan & 0xFF if scan != -1 and ord(ch) < 128 else 0, ch)
    return recs


def main(req: dict) -> dict:
    pid = int(req["pid"])
    programs = descendants(pid)
    attach(pid)
    try:
        if req["op"] == "read":
            return dict(read_screen(int(req.get("lines", 40))), programs=programs)
        if req["op"] == "type":
            recs = records(*KEYS["esc"]) if req.get("clear") else []
            recs += text_records(req["text"])
            if req.get("submit", True):
                recs += records(*KEYS["enter"])
            write_input(recs)
            return {"ok": True}
        if req["op"] == "key":
            key = req["key"]
            if key == "ctrl+c":
                kernel32.GenerateConsoleCtrlEvent(0, 0)       # CTRL_C_EVENT to everything on that console
            else:
                write_input(records(*KEYS[key]))
            return {"ok": True}
        if req["op"] == "font":
            set_font(req["face"])
            return {"ok": True}
        raise ValueError(f"unknown op {req['op']}")
    finally:
        kernel32.FreeConsole()


if __name__ == "__main__":
    try:
        result = main(json.loads(sys.argv[1]))
    except Exception as e:
        result = {"error": str(e)}
    sys.stdout.write(json.dumps(result))
