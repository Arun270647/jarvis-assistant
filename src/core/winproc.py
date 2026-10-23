"""Windows process launching without stray console windows.

Root cause of the "CMD window pops up" problem: when Jarvis runs under
pythonw.exe it has no console, so Windows gives every *console-subsystem*
child (claude.exe, cmd.exe, shutdown.exe, powershell.exe,
.cmd/.bat launchers) a brand-new visible console window. Background helpers
must be created with CREATE_NO_WINDOW. Apps the user asked to open are
launched normally (GUI apps never get a console; a console app the user
explicitly asked for gets its own window on purpose).

Every subprocess in Jarvis goes through this module.
"""
import ctypes
import os
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

IMAGE_SUBSYSTEM_WINDOWS_GUI = 2

# Markers a parent Claude Code session puts in its children's environment. When Jarvis was started
# from a Claude Code terminal they would leak into the programs the user opens: a `claude` typed into
# such a window thinks it is a child session (no transcript saving) and sees the parent's messaging pipe.
CLAUDE_SESSION_VARS = {"CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
                       "CLAUDE_EFFORT", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                       "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH"}


def user_env() -> dict:
    """os.environ without a parent Claude Code session's markers, for programs launched for the user."""
    return {k: v for k, v in os.environ.items() if k.upper() not in CLAUDE_SESSION_VARS}
IMAGE_SUBSYSTEM_WINDOWS_CUI = 3

Cmd = Union[str, Sequence[str]]


def hidden_flags() -> int:
    """creationflags for a background helper that must never show a console."""
    return CREATE_NO_WINDOW if IS_WINDOWS else 0


def split_command(command: Cmd) -> list:
    """Split a command string into argv without invoking a shell."""
    if isinstance(command, (list, tuple)):
        return [str(c) for c in command]
    return shlex.split(command, posix=not IS_WINDOWS)


def resolve_executable(name: str) -> Optional[str]:
    """Resolve a program name like the shell would (PATH + PATHEXT, so `code` -> code.cmd)."""
    if not name:
        return None
    p = Path(name)
    if p.is_absolute() or p.parent != Path("."):
        return str(p) if p.exists() else None
    return shutil.which(name)


def pe_subsystem(path: str) -> Optional[int]:
    """Return the PE subsystem of an .exe (2 = GUI, 3 = console), or None if unreadable."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        if head[:2] != b"MZ":
            return None
        pe_off = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe_off:pe_off + 4] != b"PE\0\0":
            return None
        # Optional header starts after the 20-byte COFF header; Subsystem is at +68.
        return struct.unpack_from("<H", head, pe_off + 4 + 20 + 68)[0]
    except Exception:
        return None


def is_console_program(path: str) -> bool:
    """True for console exes and batch launchers (anything that would get a console window)."""
    suffix = Path(path).suffix.lower()
    if suffix in (".cmd", ".bat"):
        return True
    if suffix == ".exe":
        return pe_subsystem(path) == IMAGE_SUBSYSTEM_WINDOWS_CUI
    return False


def run_hidden(command: Cmd, timeout: Optional[float] = None, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run for a background helper, never showing a console window."""
    kwargs.setdefault("creationflags", 0)
    kwargs["creationflags"] |= hidden_flags()
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(split_command(command), timeout=timeout, **kwargs)


def popen_hidden(command: Cmd, **kwargs) -> subprocess.Popen:
    """subprocess.Popen for a background helper, never showing a console window."""
    kwargs.setdefault("creationflags", 0)
    kwargs["creationflags"] |= hidden_flags()
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.Popen(split_command(command), **kwargs)


def launch_program(command: Cmd, show_console: bool = False, cwd: Optional[str] = None) -> subprocess.Popen:
    """Launch a program the user asked for, detached from Jarvis.

    - GUI exes start normally (they never get a console).
    - .cmd/.bat launchers (e.g. VS Code's `code`) run through cmd.exe with
      CREATE_NO_WINDOW: the launcher's console is hidden, the app it starts is not.
    - Console exes get CREATE_NO_WINDOW for system helpers (shutdown, rundll32 ...)
      unless show_console=True, which gives them a visible console on purpose
      (e.g. the user explicitly asked to open a console tool).
    """
    argv = split_command(command)
    if not argv:
        raise ValueError("empty command")
    exe = resolve_executable(argv[0])
    if exe is None:
        raise FileNotFoundError(f"Program not found: {argv[0]}")
    argv[0] = exe

    flags = CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    visible_console = False
    suffix = Path(exe).suffix.lower()
    if IS_WINDOWS and suffix in (".cmd", ".bat"):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        argv = [comspec, "/d", "/c"] + argv
        flags |= CREATE_NO_WINDOW
    elif IS_WINDOWS and is_console_program(exe):
        visible_console = show_console
        # A new process group would disable Ctrl+C in the user's console; its own console already isolates it.
        flags = CREATE_NEW_CONSOLE if show_console else flags | CREATE_NO_WINDOW

    # A visible console must keep its own console handles: with stdin on NUL an
    # interactive cmd.exe reads EOF and exits at once, so its window never shows.
    std = None if visible_console else subprocess.DEVNULL
    if visible_console:
        # "Ignore Ctrl+C" is inherited from whoever started Jarvis; clear it so Ctrl+C works in that console.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    return subprocess.Popen(
        argv,
        stdin=std,
        stdout=std,
        stderr=std,
        creationflags=flags,
        close_fds=True,
        cwd=cwd,
        env=user_env(),
    )


def pythonw_executable() -> str:
    """Path to pythonw.exe next to the running interpreter (falls back to sys.executable)."""
    exe = Path(sys.executable)
    candidate = exe.with_name(exe.name.replace("python", "pythonw", 1)) if "pythonw" not in exe.name.lower() else exe
    return str(candidate) if candidate.exists() else str(exe)
