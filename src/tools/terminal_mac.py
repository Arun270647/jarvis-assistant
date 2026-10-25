"""Terminal windows on macOS (Terminal.app), the counterpart of console_helper.py, and the typed-line rules for zsh.

- open: `do script` in Terminal opens a window with a shell in the folder; the tab is known by its tty
  (/dev/ttys003), which stays the same for as long as the window is open.
- read: the tab's visible text (Terminal's `contents`). What is in charge of it comes from the tty itself: `ps`
  gives the foreground process group (the shell at its prompt, or a program), `lsof` the shell's folder.
- type: `do script ... in` the tab types the line and Return, without focus or a permission beyond Automation
  (macOS asks once whether Jarvis may control Terminal). Text without Return and keys other than Ctrl+C need the
  window in front and the Accessibility permission (real key events, as desktop control sends them). Ctrl+C is
  SIGINT to the foreground process group, which is what the key does.

The same requests as console_helper ({"op": "read" | "type" | "key", ...}), plus "open", answered as dicts; errors
raise OSError.
"""
import os
import re
import shlex
import signal
import time
from pathlib import Path

from src.core import winproc
from src.core.workspace import WorkspaceError

OSASCRIPT = "/usr/bin/osascript"
SHELLS = {"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh", "csh", "nu"}
KEYCODES = {"enter": 36, "esc": 53, "tab": 48, "up": 126, "down": 125, "left": 123, "right": 124, "backspace": 51}

FIND_TAB = """
on findTab(ttyName)
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is ttyName then return {t, w}
            end repeat
        end repeat
    end tell
    error "The terminal window was closed."
end findTab
"""
OPEN = """
on run argv
    set folder to item 1 of argv
    tell application "Terminal"
        if it is running then
            set t to do script "cd " & quoted form of folder & " && clear"
        else
            activate
            repeat 50 times
                if (count of windows) > 0 then exit repeat
                delay 0.1
            end repeat
            set t to do script "cd " & quoted form of folder & " && clear" in front window
        end if
        activate
        return tty of t
    end tell
end run
"""
READ = FIND_TAB + """
on run argv
    set {t, w} to findTab(item 1 of argv)
    tell application "Terminal" to return contents of t
end run
"""
TYPE = FIND_TAB + """
on run argv
    set {t, w} to findTab(item 1 of argv)
    tell application "Terminal" to do script (item 2 of argv) in t
end run
"""
FRONT = FIND_TAB + """
on run argv
    set {t, w} to findTab(item 1 of argv)
    tell application "Terminal"
        set selected of t to true
        set index of w to 1
        activate
    end tell
end run
"""


def _script(source: str, *args: str, timeout: float = 15) -> str:
    r = winproc.run_hidden([OSASCRIPT, "-"] + list(args), input=source, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        message = (r.stderr or "").strip().splitlines()[-1:] or ["osascript failed"]
        if "-1743" in message[0]:
            raise OSError("macOS hasn't allowed Jarvis to control Terminal: System Settings › Privacy & Security › "
                          "Automation › Jarvis › turn on Terminal.")
        raise OSError(re.sub(r"^\d+:\d+: execution error: ", "", message[0]))
    return r.stdout.rstrip("\n")


# ------------------------------------------------------------------ what runs in the tab

def tty_processes(tty: str) -> list:
    """[{pid, pgid, tpgid, name}] for the processes on a tty ("/dev/ttys003")."""
    r = winproc.run_hidden(["/bin/ps", "-t", Path(tty).name, "-o", "pid=,pgid=,tpgid=,comm="], capture_output=True,
                           text=True, timeout=10)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit():
            out.append({"pid": int(parts[0]), "pgid": int(parts[1]), "tpgid": int(parts[2]),
                        "name": Path(parts[3].strip()).name.lstrip("-")})
    return out


def state(processes: list) -> dict:
    """{"shell": pid or None, "programs": [names in the foreground]} from tty_processes(); no programs = the prompt."""
    shells = [p for p in processes if p["name"] in SHELLS]
    shell = min(shells, key=lambda p: p["pid"]) if shells else None
    if not processes:
        return {"shell": None, "programs": []}
    foreground = processes[0]["tpgid"]
    if shell and foreground == shell["pgid"]:
        return {"shell": shell["pid"], "programs": []}
    return {"shell": shell["pid"] if shell else None,
            "programs": [p["name"] for p in processes if p["pgid"] == foreground and p["name"] != "login"]}


def folder_of(pid: int) -> str:
    r = winproc.run_hidden(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True,
                           text=True, timeout=10)
    return next((line[1:] for line in r.stdout.splitlines() if line.startswith("n")), "")


class TabProcess:
    """Stands in for the Popen of a Windows console: poll() is None while the tab's shell is alive."""

    def __init__(self, tty: str, pid: int = 0):
        self.tty, self.pid = tty, pid

    def poll(self):
        try:
            return None if tty_processes(self.tty) else 0
        except (OSError, winproc.subprocess.SubprocessError):
            return 0


# ------------------------------------------------------------------ requests

def _needs_accessibility() -> None:
    from src.platform import current
    if current().permission("accessibility") != "granted":
        current().request_permission("accessibility")
        raise OSError("macOS hasn't allowed Jarvis to press keys in Terminal yet: System Settings › Privacy & "
                      "Security › Accessibility › turn on Jarvis.")


def request(req: dict, timeout: float = 15) -> dict:
    op = req.get("op")
    if op == "open":
        tty = _script(OPEN, str(req["folder"]), timeout=timeout)
        for _ in range(40):                 # the shell takes a moment to start
            st = state(tty_processes(tty))
            if st["shell"]:
                return {"tty": tty, "pid": st["shell"]}
            time.sleep(0.1)
        return {"tty": tty, "pid": 0}
    tty = req["tty"]
    if op == "read":
        text = _script(READ, tty, timeout=timeout)
        lines = [line.rstrip() for line in text.split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        st = state(tty_processes(tty))
        cwd = folder_of(st["shell"]) if st["shell"] and not st["programs"] else ""
        last = lines[-1] if lines else ""
        return {"lines": lines[-int(req.get("lines", 40)):], "before_cursor": last, "after_cursor": "",
                "programs": st["programs"], "cwd": cwd}
    if op == "type":
        if req.get("submit", True):
            _script(TYPE, tty, req["text"], timeout=timeout)
            return {"ok": True}
        _needs_accessibility()
        _script(FRONT, tty, timeout=timeout)
        time.sleep(0.2)
        from src.tools.desktop_mac import type_unicode
        type_unicode(req["text"])
        return {"ok": True}
    if op == "key":
        key = req["key"]
        if key == "ctrl+c":
            processes = tty_processes(tty)
            if processes:
                os.killpg(processes[0]["tpgid"], signal.SIGINT)
            return {"ok": True}
        if key not in KEYCODES:
            raise OSError(f"Unknown key: {key}")
        _needs_accessibility()
        _script(FRONT, tty, timeout=timeout)
        time.sleep(0.2)
        from src.tools.desktop_mac import press_combo
        press_combo([KEYCODES[key]])
        return {"ok": True}
    if op == "font":
        return {"ok": True}                 # Terminal keeps the user's profile
    raise OSError(f"Unknown request: {op}")


# ------------------------------------------------------------------ typed-line rules (zsh)

CHAINING = re.compile(r"[;&|<>`\r\n]|\$\(|\\$")
CD_COMMANDS = {"cd", "pushd"}
LAUNCHERS = {"open", "nohup", "exec", "command", "builtin", "env", "time", "nice", "xargs", "caffeinate"}
HARMLESS = {"ls", "pwd", "echo", "clear", "which", "whoami", "tree", "exit", "date", "cat", "head", "tail", "less",
            "more", "wc", "file", "popd", "dirs", "history", "true"}
SETTERS = {"export", "unset", "alias", "unalias", "set", "setopt", "unsetopt", "source", ".", "ulimit", "umask",
           "hash", "bindkey", "typeset", "declare"}
FILE_CHANGERS = {"rm", "rmdir", "mv", "cp", "mkdir", "ln", "chmod", "chown", "chflags", "touch", "rsync", "ditto",
                 "install", "unlink", "truncate", "tee"}


def classify_line(ws, line: str, cwd: Path) -> tuple:
    """('allow' | 'ask' | 'deny', reason) for a command line typed at a zsh prompt in cwd."""
    if CHAINING.search(line):
        return "deny", "chaining, redirection and command substitution (; & | < > ` $( ) are not allowed in typed commands"
    try:
        argv = shlex.split(line)
    except ValueError:
        return "deny", "unbalanced quotes"
    if not argv:
        return "deny", "empty command"

    def at(a: str) -> Path:
        p = Path(os.path.expanduser(a))
        return p if p.is_absolute() else cwd / p

    for a in argv:
        if ws.is_secret(Path(a)):
            return "deny", f"'{a}' looks like a secrets file"
    exe, args = Path(argv[0]).name.lower(), argv[1:]
    if exe in CD_COMMANDS:
        target = next((a for a in args if not a.startswith("-")), "~")
        if target == "-":
            return "deny", "cd - goes back to a folder Jarvis can't check"
        try:
            ws.resolve(str(at(target)))
        except WorkspaceError as e:
            return "deny", str(e)
        return "allow", "changes folder"
    if exe in LAUNCHERS:
        rest = [a for a in args if not a.startswith("-")]
        if rest and exe != "open":
            verdict, why = classify_line(ws, shlex.join(rest), cwd)
            if verdict == "deny":
                return verdict, why
        return "ask", f"{exe} launches another program"
    if exe in HARMLESS:
        return "allow", "read-only shell command"
    if exe in SETTERS:
        return "ask", "changes the shell's settings or environment"
    if exe in FILE_CHANGERS:
        for a in args:
            if a.startswith("-"):
                continue
            try:
                ws.resolve(str(at(a)))
                ws.check_writable(at(a))
            except WorkspaceError as e:
                return "deny", str(e)
    verdict = ws.classify_command(argv, cwd)
    if verdict == "deny":
        return "deny", "on the denied command list"
    problem = ws.branch_problem(argv, cwd)
    if problem:
        return "deny", problem
    if verdict == "allow" and re.search(r"[$*?~{]", line):
        return "ask", "the shell expands $variables, ~ and wildcards in it"
    return verdict, "pre-approved command" if verdict == "allow" else "not on the pre-approved command list"
