"""Thread-based transport for the Claude Agent SDK.

Why this exists: on Windows, once `sounddevice` has been imported, asyncio's
subprocess support (named overlapped pipes) fails with WinError 50 when it
tries to make the pipe handles inheritable. Plain `subprocess.Popen`
(anonymous pipes) keeps working. The SDK's default transport uses anyio /
asyncio subprocesses, so it cannot start the CLI inside the Jarvis pipeline,
where the microphone, VAD and TTS all live on sounddevice.

This transport reuses the SDK's command building and output parsing, but
spawns the CLI with Popen and pumps stdout through a reader thread into an
asyncio queue.
"""
import asyncio
import subprocess
import sys
import threading
from typing import Any, AsyncIterator

from claude_agent_sdk._errors import CLIConnectionError
from claude_agent_sdk._version import __version__
from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport,
    _parse_stdout_line,
    _SDK_READS_SESSION_STATE_ENV,
)

from src.core import winproc


class ThreadedCLITransport(SubprocessCLITransport):
    """SubprocessCLITransport with Popen + reader thread instead of anyio subprocess."""

    def __init__(self, prompt, options):
        super().__init__(prompt, options)
        self._proc: subprocess.Popen | None = None
        self._queue: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: threading.Thread | None = None
        self._stdin_lock = threading.Lock()

    # --- lifecycle ---

    async def connect(self) -> None:
        if self._proc is not None:
            return
        if self._cli_path is None:
            self._cli_path = self._find_cli()
        self._reject_windows_batch_cli(self._cli_path)
        cmd = self._build_command()

        env = winproc.user_env()               # not a child of whatever Claude Code session started Jarvis
        env.update({
            "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
            **self._options.env,
            "CLAUDE_AGENT_SDK_VERSION": __version__,
        })
        env.setdefault(_SDK_READS_SESSION_STATE_ENV, "1")
        if self._cwd:
            env["PWD"] = self._cwd

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self._cwd,
                env=env,
                bufsize=0,
                creationflags=creationflags,
            )
        except Exception as e:
            raise CLIConnectionError(f"Failed to start Claude Code: {e}") from e

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._reader = threading.Thread(target=self._pump_stdout, name="claude-cli-stdout", daemon=True)
        self._reader.start()
        self._ready = True

    def _pump_stdout(self):
        proc, loop, q = self._proc, self._loop, self._queue
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                if len(line) > self._max_buffer_size:
                    loop.call_soon_threadsafe(q.put_nowait, CLIConnectionError(
                        f"JSON message exceeded maximum buffer size of {self._max_buffer_size} bytes"))
                    break
                loop.call_soon_threadsafe(q.put_nowait, line)
        except Exception as e:  # pipe closed while we are shutting down
            loop.call_soon_threadsafe(q.put_nowait, e)
        finally:
            try:
                loop.call_soon_threadsafe(q.put_nowait, None)
            except RuntimeError:
                pass  # loop already closed

    async def write(self, data: str) -> None:
        if not self._ready or self._proc is None or self._proc.stdin is None:
            raise CLIConnectionError("ProcessTransport is not ready for writing")
        if self._proc.poll() is not None:
            raise CLIConnectionError(f"Cannot write to terminated process (exit code: {self._proc.returncode})")
        payload = data.encode("utf-8")
        try:
            with self._stdin_lock:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
        except Exception as e:
            self._ready = False
            raise CLIConnectionError(f"Failed to write to process stdin: {e}") from e

    async def end_input(self) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            with self._stdin_lock:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._read_impl()

    async def _read_impl(self) -> AsyncIterator[dict[str, Any]]:
        if self._proc is None or self._queue is None:
            raise CLIConnectionError("Not connected")
        while True:
            item = await self._queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            data = _parse_stdout_line(item)
            if data is not None:
                yield data
        code = self._proc.poll()
        if code not in (None, 0) and self._ready:
            self._ready = False
            raise CLIConnectionError(f"Claude Code exited with code {code}")

    async def close(self) -> None:
        self._ready = False
        proc = self._proc
        if proc is None:
            return
        await self.end_input()
        if proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.get_running_loop().run_in_executor(None, proc.wait, 5)
            except Exception:
                proc.kill()
        self._proc = None

    def is_ready(self) -> bool:
        return self._ready and self._proc is not None and self._proc.poll() is None
