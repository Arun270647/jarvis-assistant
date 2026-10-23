"""Command Prompts Jarvis opens and types into: the typed-line policy, the approval flow with a
fake console, and the real console helper against a hidden cmd.exe."""
import ctypes
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.core import winproc
from src.core.workspace import JARVIS_ROOT, WorkspacePolicy
from src.tools import terminal_tools
from src.tools.approvals import ApprovalBroker
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry
from src.tools.workspace_tools import register_workspace_tools


class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.code = None

    def poll(self):
        return self.code


class FakeConsole:
    """Stands in for console_helper: a prompt in `cwd`, or a running program."""

    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.programs = []
        self.sent = []

    def __call__(self, req, timeout=15):
        if req["op"] == "read":
            before = "" if self.programs else self.cwd + ">"
            return {"lines": ["Microsoft Windows", before], "before_cursor": before, "after_cursor": "",
                    "programs": list(self.programs)}
        self.sent.append(req)
        return {"ok": True}


class TypedLinePolicyTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = self.dir / "proj"
        self.proj.mkdir()
        self.profile = {"projects": {"p": {"path": str(self.proj), "commands": ["npm test"],
                                           "deny_commands": ["npm publish"]}}}

    def verdict(self, line, full=False):
        ws = WorkspacePolicy(self.profile, {"agent": {"full_access": full}})
        return terminal_tools.classify_line(ws, line, self.proj)[0]

    def test_commands_follow_run_command_rules(self):
        for line, want in [("npm test", "allow"), ("git status", "allow"), ("dir", "allow"), ("cd sub", "allow"),
                           ("python evil.py", "ask"), ("npm test %X%", "ask"), ("npm publish", "deny"),
                           ("powershell -c x", "deny"), ("git push --force", "deny"), ("cmd /k claude", "deny")]:
            self.assertEqual(self.verdict(line), want, line)

    def test_cmd_specific_rules(self):
        for line, want in [("npm test & del x", "deny"), ("echo hi > f.txt", "deny"), ("dir | more", "deny"),
                           ("type .env", "deny"), ("cd C:\\Windows", "deny"), ("cd..", "deny"), ("c:", "deny"),
                           ("start powershell", "deny"), ('start "title" cmd', "deny"), ("start notepad", "ask"),
                           ("set PATH=x", "ask"), ('echo "unbalanced', "deny")]:
            self.assertEqual(self.verdict(line), want, line)

    def test_full_access_still_protects_jarvis(self):
        self.assertEqual(self.verdict("python build.py", full=True), "allow")
        self.assertEqual(self.verdict("cd C:\\Windows", full=True), "allow")
        self.assertEqual(self.verdict(f"del {JARVIS_ROOT / 'src' / 'pipeline.py'}", full=True), "deny")
        self.assertEqual(self.verdict("powershell -c x", full=True), "deny")


class TerminalToolFlowTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = (self.dir / "proj")
        self.proj.mkdir()
        self.cfg = {"agent": {}, "tools": {"permissions": {}, "approval_timeout_ms": 1500}}
        self.reg = ToolRegistry()
        register_workspace_tools(self.reg, {"projects": {"p": {"path": str(self.proj), "commands": ["npm test"]}}},
                                 self.cfg)
        self.broker = ApprovalBroker()
        self.asked = []
        self.ex = PolicyExecutor(self.reg, lambda: self.cfg, self.broker, None)
        self.console = FakeConsole(self.proj.resolve())
        self.launched = []
        self.proc = FakeProc()
        patches = [(terminal_tools, "console", self.console),
                   (terminal_tools.winproc, "launch_program", lambda argv, **k: self.launched.append((argv, k)) or self.proc)]
        for obj, name, value in patches:
            orig = getattr(obj, name)
            setattr(obj, name, value)
            self.addCleanup(setattr, obj, name, orig)
        terminal_tools._TERMINALS.clear()
        self.addCleanup(terminal_tools._TERMINALS.clear)

    def answer(self, allowed):
        def prompter(req):
            self.asked.append(req)
            threading.Timer(0.02, lambda: self.broker.resolve(req.id, allowed, via="test")).start()
        self.broker.prompter = prompter

    def run_tool(self, name, args):
        return self.ex.execute(name, args, ToolContext(task_id=1))

    def test_open_terminal_only_in_authorized_folders(self):
        r = self.run_tool("open_terminal", {"project": "p"})
        self.assertTrue(r.ok, r.content)
        self.assertEqual(r.data["terminal"], 1)
        argv, kw = self.launched[0]
        self.assertTrue(argv[0].lower().endswith("cmd.exe"))
        self.assertEqual(len(argv), 1)
        self.assertTrue(kw["show_console"])
        self.assertEqual(Path(kw["cwd"]), self.proj.resolve())
        r = self.run_tool("open_terminal", {"project": "p", "path": "C:\\Windows"})
        self.assertEqual(r.status, "denied")
        self.assertEqual(len(self.launched), 1)

    def test_pre_approved_command_is_typed_without_asking(self):
        self.answer(False)
        self.run_tool("open_terminal", {"project": "p"})
        r = self.run_tool("terminal_type", {"text": "npm test"})
        self.assertTrue(r.ok, r.content)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.console.sent, [{"op": "type", "pid": 4242, "text": "npm test", "submit": True,
                                              "clear": True}])
        self.assertIn("back at the prompt", r.content)

    def test_other_commands_ask_and_a_no_types_nothing(self):
        self.run_tool("open_terminal", {"project": "p"})
        self.answer(False)
        r = self.run_tool("terminal_type", {"text": "python evil.py"})
        self.assertEqual(r.status, "denied")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.console.sent, [])
        self.answer(True)
        r = self.run_tool("terminal_type", {"text": "python evil.py"})
        self.assertTrue(r.ok, r.content)
        self.assertEqual(len(self.console.sent), 1)

    def test_refused_lines_never_ask(self):
        self.answer(True)
        self.run_tool("open_terminal", {"project": "p"})
        for line in ("npm test & del x", "powershell", "cd C:\\Windows", "type .env"):
            r = self.run_tool("terminal_type", {"text": line})
            self.assertEqual(r.status, "denied", line)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.console.sent, [])

    def test_input_for_a_running_program_always_asks(self):
        self.run_tool("open_terminal", {"project": "p"})
        self.console.programs = ["claude.exe"]
        self.answer(True)
        r = self.run_tool("terminal_type", {"text": "npm test", "wait_s": 0})
        self.assertTrue(r.ok, r.content)
        self.assertEqual(len(self.asked), 1)
        rows = {row["k"]: row["v"] for row in self.asked[0].rows}
        self.assertIn("claude.exe", json.dumps(self.asked[0].rows))
        self.assertEqual(rows["Text"], "npm test")
        self.assertFalse(self.console.sent[0]["clear"])           # Esc would reach the program

    def test_keys_ctrl_c_free_others_ask(self):
        self.run_tool("open_terminal", {"project": "p"})
        self.answer(False)
        r = self.run_tool("terminal_key", {"key": "ctrl+c"})
        self.assertTrue(r.ok, r.content)
        r = self.run_tool("terminal_key", {"key": "enter"})
        self.assertEqual(r.status, "denied")
        self.assertEqual([s["key"] for s in self.console.sent], ["ctrl+c"])

    def test_only_open_terminals_opened_by_jarvis(self):
        r = self.run_tool("terminal_type", {"text": "dir"})
        self.assertFalse(r.ok)
        self.assertIn("open_terminal", r.content)
        self.run_tool("open_terminal", {"project": "p"})
        self.proc.code = 0                       # the user closed the window
        r = self.run_tool("terminal_read", {})
        self.assertFalse(r.ok)
        self.assertEqual(self.console.sent, [])

    def test_control_characters_are_rejected(self):
        self.run_tool("open_terminal", {"project": "p"})
        r = self.run_tool("terminal_type", {"text": "dir\r\ndel x"})
        self.assertEqual(r.status, "invalid")


@unittest.skipUnless(sys.platform == "win32", "Windows consoles")
class RealConsoleHelperTests(unittest.TestCase):
    def test_type_read_and_ctrl_c_in_a_hidden_console(self):
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)     # as launch_program does for visible consoles
        si = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=0)
        proc = subprocess.Popen(["cmd.exe"], cwd=tempfile.gettempdir(), startupinfo=si,
                                creationflags=winproc.CREATE_NEW_CONSOLE)
        self.addCleanup(proc.kill)

        def read():
            for _ in range(20):
                try:
                    return terminal_tools.console({"op": "read", "pid": proc.pid, "lines": 10})
                except OSError:
                    time.sleep(0.25)
            self.fail("could not attach to the console")

        def until(cond):
            for _ in range(40):
                s = read()
                if cond(s):
                    return s
                time.sleep(0.25)
            self.fail(f"console never reached the expected state: {s}")

        until(lambda s: s["before_cursor"].endswith(">"))
        terminal_tools.console({"op": "type", "pid": proc.pid, "text": "echo jarvis-ünïcode-test", "clear": True})
        until(lambda s: "jarvis-ünïcode-test" in s["lines"][-3:])
        terminal_tools.console({"op": "type", "pid": proc.pid, "text": "ping -n 30 127.0.0.1", "clear": True})
        until(lambda s: "ping.exe" in s["programs"])
        terminal_tools.console({"op": "key", "pid": proc.pid, "key": "ctrl+c"})
        s = until(lambda s: not s["programs"] and s["before_cursor"].endswith(">"))
        self.assertIsNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
