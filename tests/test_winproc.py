"""Hidden-console process handling (the root cause of the CMD windows)."""
import ast
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from src.core import winproc

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WinprocTests(unittest.TestCase):
    def test_pe_subsystem_detection(self):
        cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        notepad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        self.assertEqual(winproc.pe_subsystem(cmd), winproc.IMAGE_SUBSYSTEM_WINDOWS_CUI)
        self.assertTrue(winproc.is_console_program(cmd))
        if os.path.exists(notepad):
            self.assertEqual(winproc.pe_subsystem(notepad), winproc.IMAGE_SUBSYSTEM_WINDOWS_GUI)
            self.assertFalse(winproc.is_console_program(notepad))
        self.assertIsNone(winproc.pe_subsystem(__file__))

    def test_claude_cli_is_a_console_program(self):
        claude = shutil.which("claude")
        if not claude:
            self.skipTest("claude CLI not installed")
        # This is why delegation used to open a CMD window under pythonw.
        self.assertTrue(winproc.is_console_program(claude))

    def test_hidden_flags(self):
        self.assertTrue(winproc.hidden_flags() & winproc.CREATE_NO_WINDOW)

    def test_run_hidden_executes_console_program(self):
        r = winproc.run_hidden(["cmd", "/c", "echo", "jarvis"], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertIn("jarvis", r.stdout)

    def test_split_command_without_shell(self):
        self.assertEqual(winproc.split_command("rundll32.exe user32.dll,LockWorkStation"),
                         ["rundll32.exe", "user32.dll,LockWorkStation"])
        self.assertEqual(winproc.split_command(["a", 1]), ["a", "1"])

    def test_launch_program_resolves_cmd_shims_hidden(self):
        # A .cmd launcher (like VS Code's `code`) must go through cmd.exe with CREATE_NO_WINDOW.
        import tempfile
        d = Path(tempfile.mkdtemp())
        shim = d / "jarvis_test_shim.cmd"
        marker = d / "ran.txt"
        shim.write_text(f'@echo off\r\necho ok> "{marker}"\r\n', encoding="ascii")
        proc = winproc.launch_program([str(shim)])
        proc.wait(timeout=20)
        self.assertTrue(marker.exists())
        self.assertTrue(proc.args[0].lower().endswith("cmd.exe"))

    def test_visible_console_keeps_its_own_std_handles(self):
        # open_terminal regression: with stdin on DEVNULL an interactive cmd.exe reads EOF
        # and exits at once, so the "opened" Command Prompt never showed a window.
        calls = []
        orig = winproc.subprocess.Popen
        winproc.subprocess.Popen = lambda argv, **k: calls.append(k)
        try:
            winproc.launch_program(["cmd.exe"], show_console=True)
            winproc.launch_program(["cmd.exe"])
        finally:
            winproc.subprocess.Popen = orig
        shown, hidden = calls
        self.assertTrue(shown["creationflags"] & winproc.CREATE_NEW_CONSOLE)
        self.assertIsNone(shown["stdin"])
        self.assertIsNone(shown["stdout"])
        self.assertTrue(hidden["creationflags"] & winproc.CREATE_NO_WINDOW)
        self.assertEqual(hidden["stdin"], subprocess.DEVNULL)

    def test_launch_program_missing(self):
        with self.assertRaises(FileNotFoundError):
            winproc.launch_program(["definitely-not-a-real-program-xyz"])


class NoUnflaggedSubprocessTests(unittest.TestCase):
    """Static guard: every subprocess call in app code must go through winproc (or pass creationflags)."""

    FILES = ["src", "scripts", "jarvis_gui.pyw"]
    ALLOWED = {Path("src/core/winproc.py")}

    def iter_files(self):
        for f in self.FILES:
            p = ROOT / f
            if p.is_dir():
                yield from p.rglob("*.py")
            elif p.exists():
                yield p

    def test_no_raw_subprocess_without_creationflags(self):
        offenders = []
        for path in self.iter_files():
            rel = path.relative_to(ROOT)
            if rel in self.ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ""
                if owner == "subprocess" and name in ("Popen", "run", "call", "check_call", "check_output"):
                    if not any(k.arg == "creationflags" for k in node.keywords):
                        offenders.append(f"{rel}:{node.lineno} subprocess.{name}")
                if owner == "os" and name in ("system", "popen"):
                    offenders.append(f"{rel}:{node.lineno} os.{name}")
                if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
                    offenders.append(f"{rel}:{node.lineno} shell=True")
        self.assertEqual(offenders, [], "Subprocess calls that can open a console window:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
