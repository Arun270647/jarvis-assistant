"""Command Prompt windows Jarvis opens for the user, and typing into them under the command policy.

- open_terminal opens a visible cmd.exe in a folder and gives it a terminal number. Only these
  windows can be typed into; other consoles stay blocked for desktop control.
- Input goes straight into the console's input buffer and the screen is read back
  (src/tools/console_helper.py, a short-lived hidden process: attaching to a console is
  process-wide and would hand Jarvis that console's Ctrl+C). No focus or real keyboard needed.
- At the prompt, a typed line is a command: it gets run_command's rules (deny list, project
  deny_commands, commit branches, allowed / ask) plus cmd's own: no chaining or redirection
  (& | < > ^), cd only into authorized folders, del/move/copy... never into protected paths,
  secret files refused, start/call/set ask. While a program runs in the window (claude, python,
  a dev server), typed text is input for that program and always asks, as does every key but Ctrl+C.
"""
import json
import os
import re
import shlex
import sys
import threading
import time
from pathlib import Path

from src.core import winproc
from src.core.redact import redact
from src.core.workspace import WorkspaceError
from src.tools.registry import ToolResult, ToolSpec

HELPER = Path(__file__).with_name("console_helper.py")
PROMPT = re.compile(r"^([A-Za-z]:\\[^>]*)>(.*)$")
CHAINING = re.compile(r"[&|<>^\r\n]")
CD_COMMANDS = {"cd", "chdir", "pushd"}
LAUNCHERS = {"start", "call"}
HARMLESS = {"dir", "type", "echo", "cls", "ver", "vol", "where", "title", "popd", "tree", "more", "exit"}
SETTERS = {"set", "setx", "path", "prompt", "doskey", "assoc", "ftype", "color", "mode", "chcp"}
FILE_CHANGERS = {"del", "erase", "rd", "rmdir", "move", "ren", "rename", "copy", "xcopy", "robocopy", "mkdir",
                 "md", "mklink", "attrib", "replace"}
KEYS = ["ctrl+c", "enter", "esc", "tab", "up", "down", "left", "right", "backspace"]

# Terminals opened by Jarvis in this app run: number -> {"proc", "pid", "folder"}. Kept across
# pipeline restarts; the windows belong to the user and stay open when Jarvis exits.
_TERMINALS = {}
_LOCK = threading.Lock()


def _python() -> str:
    exe = Path(sys.executable)
    console = exe.with_name(exe.name.lower().replace("pythonw", "python"))
    return str(console if console.exists() else exe)


def console(req: dict, timeout: float = 15) -> dict:
    """Run one console_helper request. Raises OSError with the helper's message on failure."""
    r = winproc.run_hidden([_python(), "-I", str(HELPER), json.dumps(req)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise OSError((r.stderr or "").strip()[-300:] or "the console helper gave no answer")
    if "error" in out:
        raise OSError(out["error"])
    return out


def _unquote(tok: str) -> str:
    return tok[1:-1] if len(tok) >= 2 and tok[0] == tok[-1] == '"' else tok


def classify_line(ws, line: str, cwd: Path) -> tuple:
    """('allow' | 'ask' | 'deny', reason) for a command line typed at a cmd prompt in cwd."""
    if CHAINING.search(line):
        return "deny", "chaining and redirection (& | < > ^) are not allowed in typed commands"
    try:
        argv = [_unquote(t) for t in shlex.split(line, posix=False)]
    except ValueError:
        return "deny", "unbalanced quotes"
    if not argv:
        return "deny", "empty command"
    m = re.match(r"^(cd|chdir)([.\\].*)$", argv[0], re.I)          # cd.. and cd\ without a space
    if m:
        argv = [m.group(1), m.group(2)] + argv[1:]

    def at(a: str) -> Path:
        p = Path(a)
        return p if p.is_absolute() else cwd / p

    for a in argv:
        if ws.is_secret(Path(a)):
            return "deny", f"'{a}' looks like a secrets file"
    exe = argv[0].lower() if re.fullmatch(r"[a-z]:", argv[0], re.I) else \
        re.sub(r"\.(exe|cmd|bat|com)$", "", Path(argv[0]).name.lower())
    args = argv[1:]
    if re.fullmatch(r"[a-z]:", exe) or exe in CD_COMMANDS:
        target = exe + "\\" if exe not in CD_COMMANDS else " ".join(a for a in args if a.lower() != "/d")
        if not target:
            return "allow", "shows the current folder"
        try:
            ws.resolve(str(at(target)))
        except WorkspaceError as e:
            return "deny", str(e)
        return "allow", "changes folder"
    if exe in LAUNCHERS:
        rest = args[1:] if exe == "start" and args and line.split(None, 1)[1].startswith('"') else args
        rest = [a for a in rest if not a.startswith("/")]
        if rest:
            verdict, why = classify_line(ws, " ".join(f'"{a}"' if " " in a else a for a in rest), cwd)
            if verdict == "deny":
                return verdict, why
        return "ask", f"{exe} launches another program"
    if exe in HARMLESS:
        return "allow", "read-only cmd command"
    if exe in SETTERS:
        return "ask", "changes the console's settings or environment"
    if exe in FILE_CHANGERS:
        for a in args:
            if a.startswith("/"):
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
    if verdict == "allow" and "%" in line:
        return "ask", "cmd expands %variables% in it"
    return verdict, "pre-approved command" if verdict == "allow" else "not on the pre-approved command list"


def register_terminal_tools(reg, ws, config: dict) -> None:
    where = "on this PC" if ws.full_access else "in an authorized project"
    projects = sorted(ws.projects)
    checked = {}            # (terminal, text) -> the verdict needs_confirmation saw, to catch a change before typing

    def find(num) -> tuple:
        """(number, info, error) for a terminal number, or the newest one still open."""
        with _LOCK:
            alive = {n: t for n, t in _TERMINALS.items() if t["proc"].poll() is None}
        if num is None:
            if not alive:
                return None, None, "No Command Prompt opened by Jarvis is open. Use open_terminal first."
            num = max(alive)
        if num not in alive:
            return None, None, (f"Terminal {num} is closed. Open terminals: " + ", ".join(map(str, sorted(alive)))
                                if alive else f"Terminal {num} is closed. Use open_terminal to open a new one.")
        return num, alive[num], ""

    def look(info, lines: int = 40) -> dict:
        """The window's screen and what is in charge of it: the cmd prompt (with its folder) or a program."""
        s = console({"op": "read", "pid": info["pid"], "lines": lines})
        m = PROMPT.match(s["before_cursor"].rstrip(" ")) if not s["after_cursor"] else None
        if s["programs"]:
            s["mode"], s["cwd"] = "program", None
        elif m:
            s["mode"], s["cwd"] = "prompt", m.group(1)
        else:
            s["mode"], s["cwd"] = "unknown", None
        return s

    def evaluate(a) -> dict:
        num, info, err = find(a.get("terminal"))
        if err:
            return {"verdict": "deny", "why": err, "error": True}
        try:
            s = look(info)
        except Exception as e:
            return {"verdict": "deny", "why": f"Could not read terminal {num}: {e}", "error": True}
        ev = {"num": num, "info": info, "screen": s}
        if s["mode"] == "program":
            ev.update(verdict="ask", why=f"input for the running program ({', '.join(s['programs'][:3])})")
        elif s["mode"] == "unknown":
            ev.update(verdict="ask", why="the window is not at a normal prompt, so the folder is unknown")
        else:
            try:
                cwd = ws.resolve(s["cwd"])
            except WorkspaceError as e:
                ev.update(verdict="deny", why=str(e))
                return ev
            verdict, why = classify_line(ws, a["text"], cwd)
            ev.update(verdict=verdict, why=why)
        return ev

    def screen_text(s: dict, limit: int = 40) -> str:
        body = "\n".join(s["lines"][-limit:]).strip("\n")
        state = (f"still running: {', '.join(s['programs'][:3])}" if s["mode"] == "program"
                 else f"back at the prompt in {s['cwd']}" if s["mode"] == "prompt" else "state unclear")
        return f"Screen (last lines, {state}):\n{redact(body) or '(empty)'}"

    def settle(info, cancel_event, wait_s: float) -> dict:
        """Wait until the window is back at its prompt (the command finished) or wait_s is up."""
        time.sleep(0.4)
        deadline = time.time() + wait_s
        s = look(info)
        while s["mode"] != "prompt" and time.time() < deadline and not cancel_event.is_set():
            time.sleep(0.5)
            s = look(info)
        return s

    # ---------------------------------------------------------------- open

    def open_terminal(a, c):
        try:
            folder = ws.resolve(a.get("path") or ".", a.get("project"))
        except WorkspaceError as e:
            return ToolResult(ok=False, status="denied", content=str(e))
        if not folder.is_dir():
            return ToolResult(ok=False, status="failed", content=f"Not a folder: {folder}")
        if config.get("dry_run"):
            return ToolResult(ok=True, status="dry_run", dry_run=True, content=f"Would open Command Prompt in {folder}")
        try:
            proc = winproc.launch_program([os.environ.get("COMSPEC", "cmd.exe")], show_console=True, cwd=str(folder))
        except (OSError, ValueError) as e:
            return ToolResult(ok=False, status="failed", content=f"Could not open Command Prompt: {e}")
        with _LOCK:
            num = max(_TERMINALS, default=0) + 1
            _TERMINALS[num] = {"proc": proc, "pid": proc.pid, "folder": str(folder)}
        for _ in range(20):                      # the console takes a moment before it can be typed into
            try:
                look(_TERMINALS[num])
                break
            except OSError:
                if proc.poll() is not None:
                    return ToolResult(ok=False, status="failed", content="The Command Prompt closed right after opening.")
                time.sleep(0.25)
        font = (config.get("terminal", {}) or {}).get("font")
        if font:
            try:
                console({"op": "font", "pid": proc.pid, "face": font})
            except OSError as e:
                print(f"[TERMINAL] Terminal {num} keeps its default font: {e}")
        return ToolResult(ok=True, content=f"Opened Command Prompt as terminal {num} in {folder}. Type commands into it "
                                           "with terminal_type, read it with terminal_read.",
                          data={"terminal": num, "path": str(folder)})

    # ---------------------------------------------------------------- type / keys / read

    def needs_ask_text(a):
        ev = evaluate(a)
        checked[(ev.get("num"), a.get("text"))] = ev["verdict"]
        return ev["verdict"] == "ask"

    def terminal_type(a, c):
        ev = evaluate(a)
        seen = checked.pop((ev.get("num"), a["text"]), None)
        if ev["verdict"] == "deny":
            return ToolResult(ok=False, status="failed" if ev.get("error") else "denied",
                              content=f"Not typed: {ev['why']}")
        if ev["verdict"] == "ask" and seen == "allow":        # passed without asking, but no longer allowed
            return ToolResult(ok=False, status="failed", content="The terminal changed since the check (a program "
                                                                 "started or finished). Look at it with terminal_read "
                                                                 "and try again.")
        num, info, s = ev["num"], ev["info"], ev["screen"]
        target = f"the running program ({', '.join(s['programs'][:3])})" if s["mode"] == "program" else "the prompt"
        if config.get("dry_run"):
            return ToolResult(ok=True, status="dry_run", dry_run=True,
                              content=f"Would type “{a['text']}” into {target} of terminal {num}")
        try:
            console({"op": "type", "pid": info["pid"], "text": a["text"], "submit": a.get("submit", True),
                     "clear": s["mode"] == "prompt"})
            s = settle(info, c.cancel_event, float(a.get("wait_s", 3))) if a.get("submit", True) else look(info)
        except OSError as e:
            return ToolResult(ok=False, status="failed", content=f"Could not type into terminal {num}: {e}")
        return ToolResult(ok=True, untrusted=True,
                          content=f"Typed “{a['text']}” into {target} of terminal {num}"
                                  + (" and pressed Enter." if a.get("submit", True) else ".") + "\n" + screen_text(s),
                          data={"terminal": num, "mode": s["mode"]})

    def terminal_key(a, c):
        num, info, err = find(a.get("terminal"))
        if err:
            return ToolResult(ok=False, status="failed", content=err)
        if config.get("dry_run"):
            return ToolResult(ok=True, status="dry_run", dry_run=True, content=f"Would press {a['key']} in terminal {num}")
        try:
            for _ in range(int(a.get("times", 1))):
                console({"op": "key", "pid": info["pid"], "key": a["key"]})
                time.sleep(0.05)
            s = settle(info, c.cancel_event, float(a.get("wait_s", 1)))
        except OSError as e:
            return ToolResult(ok=False, status="failed", content=f"Could not send {a['key']} to terminal {num}: {e}")
        return ToolResult(ok=True, untrusted=True, content=f"Pressed {a['key']} in terminal {num}.\n" + screen_text(s),
                          data={"terminal": num, "mode": s["mode"]})

    def terminal_read(a, c):
        num, info, err = find(a.get("terminal"))
        if err:
            return ToolResult(ok=False, status="failed", content=err)
        try:
            s = look(info, int(a.get("lines", 40)))
        except OSError as e:
            return ToolResult(ok=False, status="failed", content=f"Could not read terminal {num}: {e}", transient=True)
        return ToolResult(ok=True, untrusted=True, content=f"Terminal {num}. " + screen_text(s, int(a.get("lines", 40))),
                          data={"terminal": num, "mode": s["mode"], "cwd": s["cwd"]})

    def type_rows(a):
        ev = evaluate(a)
        if ev.get("error"):
            return [{"k": "Tool", "v": "terminal_type", "sub": ""}, {"k": "Text", "v": a.get("text", ""), "sub": ev["why"]}]
        s = ev["screen"]
        into = (f"Program running in it: {', '.join(s['programs'][:3])}" if s["mode"] == "program"
                else f"Prompt in {s['cwd'] or 'an unknown folder'}")
        return [{"k": "Tool", "v": "terminal_type", "sub": ""},
                {"k": "Terminal", "v": f"Command Prompt {ev['num']}", "sub": into},
                {"k": "Text", "v": a.get("text", ""), "sub": ev["why"] + ("; Enter is pressed after" if a.get("submit", True) else "")}]

    def needs_task(a):
        try:
            argv = [_unquote(t) for t in shlex.split(a.get("text", ""), posix=False)]
        except ValueError:
            return True
        return not argv or not ws.is_read_only_command(argv)

    terminal_prop = {"type": "integer", "minimum": 1,
                     "description": "Terminal number from open_terminal (default: the newest one still open)"}
    reg.register(ToolSpec(
        name="open_terminal",
        description=f"Open a visible Command Prompt window in a folder {where}. It gets a terminal number: type "
                    "commands into it with terminal_type (e.g. cd /d <folder>, claude, npm run dev) and read it with "
                    "terminal_read. This is how Claude Code runs on a project for the user (type claude, then the "
                    "task). For a command whose output you only need yourself, run_command is simpler.",
        input_schema={"type": "object", "properties": {
            "project": {"type": "string", "enum": projects, "description": "Project key (base for relative paths)"},
            "path": {"type": "string", "minLength": 1, "description": "Folder, absolute or relative to the project"}}},
        handler=open_terminal, idempotent=False, timeout_s=20,
        purpose=lambda a: f"Open Command Prompt in {a.get('path') or a.get('project')}"))
    reg.register(ToolSpec(
        name="terminal_type",
        description="Type a line into a Command Prompt that open_terminal opened, then Enter (submit=false: no "
                    "Enter). At the prompt it is a command and gets run_command's rules: pre-approved commands "
                    "are typed at once, others ask the user, dangerous ones, chaining/redirection (& | < > ^) and "
                    "cd outside authorized folders are refused. While a program runs in the window (claude, "
                    "python, a dev server), the text is input for that program and the user approves it first. "
                    "Waits up to wait_s for the command to finish, then returns the screen (untrusted output).",
        input_schema={"type": "object", "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": r"^[^\x00-\x1f\x7f]+$"},
            "terminal": terminal_prop,
            "submit": {"type": "boolean", "description": "Press Enter after the text (default true)"},
            "wait_s": {"type": "number", "minimum": 0, "maximum": 120,
                       "description": "Wait this long for the prompt to come back (default 3)"}},
            "required": ["text"]},
        handler=terminal_type, risk="sensitive", timeout_s=150, cancellable=True,
        needs_confirmation=needs_ask_text, approval_rows=type_rows,
        requires_task=False if ws.full_access else needs_task,
        purpose=lambda a: f"Type “{a.get('text', '')[:60]}” in Command Prompt"))
    reg.register(ToolSpec(
        name="terminal_key",
        description="Press a key in a Command Prompt that open_terminal opened: ctrl+c stops the running command "
                    "(e.g. a dev server) without asking; enter, esc, tab and the arrows (answering a program's menu) "
                    "ask the user first.",
        input_schema={"type": "object", "properties": {
            "key": {"type": "string", "enum": KEYS}, "terminal": terminal_prop,
            "times": {"type": "integer", "minimum": 1, "maximum": 20},
            "wait_s": {"type": "number", "minimum": 0, "maximum": 60}}, "required": ["key"]},
        handler=terminal_key, risk="sensitive", timeout_s=90, cancellable=True,
        needs_confirmation=lambda a: a.get("key") != "ctrl+c",
        approval_rows=lambda a: [{"k": "Tool", "v": "terminal_key", "sub": ""},
                                 {"k": "Key", "v": a.get("key", "") + (f" ×{a['times']}" if a.get("times", 1) > 1 else ""),
                                  "sub": f"in Command Prompt {a.get('terminal') or '(newest)'}"}],
        purpose=lambda a: f"Press {a.get('key')} in Command Prompt"))
    reg.register(ToolSpec(
        name="terminal_read",
        description="Read the last lines of a Command Prompt that open_terminal opened, and whether it is at its "
                    "prompt (with the folder) or a program is running in it.",
        input_schema={"type": "object", "properties": {
            "terminal": terminal_prop, "lines": {"type": "integer", "minimum": 5, "maximum": 200}}},
        handler=terminal_read, category="read", retries=1, timeout_s=20,
        purpose=lambda a: "Read Command Prompt"))
