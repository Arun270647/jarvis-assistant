"""LIVE check: Jarvis never shows a console window (not part of the unit-test run).

A system-wide WinEvent hook records every window that becomes visible while
the real GUI (pythonw jarvis_gui.pyw) runs through:
  1. startup (Whisper, TTS, Claude Code session = claude.exe, a console program)
  2. a typed command answered by Claude
  3. a real system command that runs a console program (shutdown.exe /a, harmless)
  4. Settings > Test connection (a second claude.exe session)
  5. an approved Claude Code delegation (claude.exe -p in the project folder)
  5b. an agentic task on sandbox/demo_shop (tests, edits, build, independent verification)
  5c. a Chrome task (the user's real Chrome profile)
  6. Quit (checks that pythonw and its claude.exe children are gone)

A control step first opens a console with CREATE_NEW_CONSOLE to prove the hook sees console windows.
Any console / terminal window that appears during steps 1-6 fails the check.

    python tests/live_no_console.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core import paths  # noqa: E402
from src.core.winproc import pythonw_executable  # noqa: E402

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
EVENT_OBJECT_SHOW = 0x8002
WINEVENT_OUTOFCONTEXT = 0x0000
OBJID_WINDOW = 0
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}
WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


def window_info(hwnd):
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            exe = Path(buf.value).name
        kernel32.CloseHandle(h)
    return {"class": cls.value, "title": title.value, "pid": pid.value, "exe": exe}


class WindowWatcher:
    def __init__(self):
        self.shown = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._tid = None

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()

        @WinEventProc
        def cb(hook, event, hwnd, id_object, id_child, thread, ms):
            if id_object != OBJID_WINDOW or not hwnd:
                return
            if user32.GetAncestor(hwnd, 2) != hwnd:  # GA_ROOT: top-level windows only
                return
            info = window_info(hwnd)
            info["t"] = round(time.time(), 2)
            self.shown.append(info)

        self._cb = cb
        hook = user32.SetWinEventHook(EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, 0, cb, 0, 0, WINEVENT_OUTOFCONTEXT)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWinEvent(hook)

    def start(self):
        self._thread.start()
        self._ready.wait(5)

    def stop(self):
        user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
        self._thread.join(5)

    def consoles(self, since=0.0):
        return [w for w in self.shown if w["t"] >= since and (w["class"] in CONSOLE_CLASSES or w["exe"].lower() in ("conhost.exe", "openconsole.exe", "windowsterminal.exe", "cmd.exe"))]


def gui(*args):
    return subprocess.run([sys.executable, str(ROOT / "jarvis_gui.pyw"), *args], capture_output=True, text=True, timeout=30)


def ui(js):
    gui("--eval", js)


def log_lines_since(mark):
    lines = (paths.log_dir() / "jarvis.log").read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[mark:]


def wait_log(mark, needle, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if any(needle in l for l in log_lines_since(mark)):
            return True
        time.sleep(0.5)
    return False


def procs(name):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return {int(l.split('","')[1]) for l in out.splitlines() if l.startswith('"')}


def main() -> int:
    results = {}
    watcher = WindowWatcher()
    watcher.start()

    # Control: a console window created on purpose must be detected.
    t = time.time()
    p = subprocess.Popen(["cmd", "/c", "ping -n 2 127.0.0.1 >nul"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    p.wait()
    time.sleep(0.5)
    results["control: hook detects a deliberate console window"] = bool(watcher.consoles(t))

    claude_before = procs("claude.exe")
    log_file = paths.log_dir() / "jarvis.log"
    mark = len(log_file.read_text(encoding="utf-8", errors="replace").splitlines()) if log_file.exists() else 0
    t_app = time.time()
    env = dict(os.environ, JARVIS_DEV="1")
    app = subprocess.Popen([pythonw_executable(), str(ROOT / "jarvis_gui.pyw")], cwd=str(ROOT), env=env)
    results["1 startup: assistant ready"] = wait_log(mark, "[READY]", 180)

    ui("document.getElementById('input').value='what is two plus two? answer in one short sentence'; document.getElementById('btn-send').click();")
    results["2 typed command answered by Claude"] = wait_log(mark, "[TIMING]", 90)

    m2 = len(log_lines_since(0))
    ui("document.getElementById('input').value='run the cancel_shutdown system command'; document.getElementById('btn-send').click();")
    results["3 console system command executed (shutdown /a)"] = wait_log(m2, "system_command({'command': 'cancel_shutdown'})", 60)
    wait_log(m2, "[TIMING]", 60)

    ui("document.querySelector('.tab[data-view=settings]').click(); setTimeout(() => document.getElementById('btn-test').click(), 1500);")
    m3 = len(log_lines_since(0))
    results["4 Settings test connection (second claude.exe)"] = wait_log(m3, "[AGENT] Claude Code session closed", 120)
    ui("document.querySelector('.tab[data-view=assistant]').click();")

    m4 = len(log_lines_since(0))
    ui("document.getElementById('input').value=\"ask claude in the jarvis project to reply with the single word ok and not change any files\"; document.getElementById('btn-send').click();")
    asked = wait_log(m4, "[APPROVAL] Asking", 90)
    time.sleep(1.0)
    ui("const b = document.querySelector('.dialog .btn-primary'); if (b) b.click();")
    results["5 approved Claude Code delegation ran (claude -p)"] = asked and wait_log(m4, "[TOOL] delegate_to_claude", 400) and \
        any("delegate_to_claude" in l and "-> done" in l for l in log_lines_since(m4))

    # 5b. An agentic task: Claude runs the demo project's tests and build (python.exe children) and edits files.
    subprocess.run([sys.executable, str(ROOT / "sandbox" / "reset_demo.py")], capture_output=True)
    m5 = len(log_lines_since(0))
    ui("document.getElementById('input').value='Prepare the demo shop project for deployment, test it, fix any issues "
       "you can safely fix, and tell me when it is ready.'; document.getElementById('btn-send').click();")
    results["5b agentic task ran tests/build and completed verification"] = wait_log(m5, "] completed: Verified", 400)
    wait_log(m5, "[TIMING]", 120)

    # 5c. A Chrome task: the user's real Chrome profile.
    m6 = len(log_lines_since(0))
    ui("document.getElementById('input').value='Open example.com in Chrome.'; "
       "document.getElementById('btn-send').click();")
    results["5c Chrome task ran (real profile)"] = wait_log(m6, "[TOOL] open_chrome", 180) and \
        any("open_chrome" in l and "-> done" in l for l in log_lines_since(m6))
    wait_log(m6, "[TIMING]", 120)

    gui("--quit")
    try:
        app.wait(40)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(2)
    results["6 quit: pythonw exited"] = app.poll() is not None
    leftover = procs("claude.exe") - claude_before
    results["6 quit: no claude.exe left behind"] = not leftover

    consoles = watcher.consoles(t_app)
    results["NO console/terminal window appeared (steps 1-6)"] = not consoles
    watcher.stop()

    app_windows = [w for w in watcher.shown if w["t"] >= t_app and w["exe"].lower() in ("pythonw.exe", "qtwebengineprocess.exe")]
    print("Windows shown by Jarvis:", json.dumps(sorted({(w["exe"], w["class"], w["title"]) for w in app_windows})))
    if consoles:
        print("CONSOLE WINDOWS:", json.dumps(consoles, indent=1))
    for k, v in results.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = all(results.values())
    print("LIVE NO-CONSOLE CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
