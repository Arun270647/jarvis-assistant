"""Claude as a tool-using agent through the Claude Code CLI (subscription login).

One persistent Claude Code session (Claude Agent SDK + ThreadedCLITransport).
The Jarvis tool registry is exposed to Claude as an in-process SDK MCP server
named "jarvis"; every tool handler calls PolicyExecutor.execute(), so schema
validation, permissions, approvals, dry-run and timeouts are enforced in this
process and cannot be bypassed by the model. Claude Code's own built-in tools
(Bash, Read, Edit, ...) are disabled, user/project settings are not loaded,
and only mcp__jarvis__* tools are allowed.

Claude reads the actual tool results and may call further tools; the number
of model turns per command is bounded by agent.max_turns.
"""
import asyncio
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from src.agent.base import AgentBackend, AgentResult
from src.agent.prompt import build_agent_system_prompt
from src.agent.providers.base import Usage
from src.agent.toolsets import tool_names
from src.tools.registry import ToolContext


def _profile() -> dict:
    try:
        from src.core.config_store import load_profile
        return load_profile()
    except Exception:
        return {}


def _settings(config: dict) -> tuple:
    """(provider key, model, settings) of the main role when it runs on Claude Code."""
    from src.agent.providers import role
    try:
        return role(config, "main")
    except Exception:
        return "claude_code", "", {}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_NAME = "jarvis"


class ClaudeCodeAgentBackend(AgentBackend):
    name = "claude_code"

    def __init__(self, config: dict, executor, meter=None):
        self.name, model, ccfg = _settings(config)
        acfg = config.get("agent", {})
        self.settings = ccfg
        self.meter = meter
        self.model = model or "claude-sonnet-5-5"
        self.effort = ccfg.get("effort") or "medium"
        self.cli_path = ccfg.get("cli_path") or None
        self.step_timeout = float(ccfg.get("timeout_seconds", 30))     # max silence from Claude between events
        self.session_reset_after = int(ccfg.get("session_reset_after", 100))
        self.max_turns = int(acfg.get("max_turns", 8))
        self.max_seconds = float(acfg.get("max_seconds", 600))
        # Within a task Claude may think for a long time before a big edit; allow a longer silence.
        self.task_step_timeout = float(acfg.get("task", {}).get("idle_timeout_seconds", 180))
        self.executor = executor
        self.tool_names = tool_names(executor.registry, config, ccfg)
        self._system_prompt = build_agent_system_prompt(
            self.tool_names, _profile(), bool(acfg.get("full_access")),
            coding_cli=(config.get("coding_agent", {}) or {}).get("cli", "claude"))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client = None
        self._turns = 0
        self._ctx = ToolContext()
        self._tools_running = 0
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._last_usage = None

    # ------------------------------------------------------------ loop plumbing

    def _start_loop(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="claude-agent-loop", daemon=True)
        self._thread.start()

    def _call(self, coro, timeout: Optional[float] = None):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def _stop_loop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._thread = None

    # ------------------------------------------------------------ MCP tools

    def _build_server(self):
        from claude_agent_sdk import create_sdk_mcp_server, tool

        tools = []
        offered = set(self.tool_names)
        for spec in self.executor.registry.specs():
            if spec.name not in offered:
                continue
            tools.append(tool(spec.name, spec.description, spec.input_schema)(self._make_handler(spec.name)))
        return create_sdk_mcp_server(name=SERVER_NAME, version="2.0.0", tools=tools)

    def _make_handler(self, name: str):
        async def handler(args):
            loop = asyncio.get_running_loop()
            self._tools_running += 1
            try:
                # The executor blocks (approvals, delegations); keep the SDK loop free.
                result = await loop.run_in_executor(None, self.executor.execute, name, dict(args or {}), self._ctx)
            finally:
                self._tools_running -= 1
            content = [{"type": "text", "text": result.as_text()}]
            for img in result.data.get("images", []) or []:     # e.g. browser screenshots
                content.append({"type": "image", "data": img["data"], "mimeType": img.get("mimeType", "image/png")})
            return {"content": content, "is_error": not result.ok}
        return handler

    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions
        allowed = [f"mcp__{SERVER_NAME}__{n}" for n in self.tool_names]
        return ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            tools=[],                       # no built-in Claude Code tools (Bash, Read, ...)
            mcp_servers={SERVER_NAME: self._build_server()},
            strict_mcp_config=True,         # ignore any other MCP config on this machine
            allowed_tools=allowed,          # gating happens in PolicyExecutor
            permission_mode="dontAsk",      # anything not allowed above is denied
            model=self.model,
            effort=self.effort,
            max_turns=self.max_turns,
            setting_sources=[],
            cwd=str(PROJECT_ROOT),
            cli_path=self.cli_path,
        )

    async def _connect(self):
        from claude_agent_sdk import ClaudeSDKClient
        from src.agent.claude_transport import ThreadedCLITransport

        async def _no_prompt():
            if False:
                yield {}

        options = self._options()
        transport = ThreadedCLITransport(_no_prompt(), options)
        self._client = ClaudeSDKClient(options, transport=transport)
        await self._client.connect()
        self._turns = 0

    async def _disconnect(self):
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    # ------------------------------------------------------------ public

    def load(self) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise RuntimeError("claude-agent-sdk not installed: pip install claude-agent-sdk") from e
        print(f"[AGENT] Starting Claude Code session ({self.model}, effort={self.effort}, "
              f"{len(self.tool_names)} tools)...")
        t0 = time.time()
        self._start_loop()
        try:
            self._call(self._connect(), timeout=60)
        except Exception as e:
            self._stop_loop()
            raise RuntimeError(f"Could not start Claude Code CLI: {e}. Is `claude` installed and logged in? "
                               "Check with: python -m src.agent check") from e
        print(f"[AGENT] Session up in {time.time() - t0:.2f}s, warming up...")
        t0 = time.time()
        try:
            res = self.run("[startup check] Reply with exactly: ready", ToolContext(), lambda ev: None)
            if not res.ok:
                raise RuntimeError(res.error)
            print(f"[AGENT] Warmup done in {time.time() - t0:.2f}s: {res.reply!r}")
        except Exception as e:
            self.unload()
            raise RuntimeError(f"Claude Code warmup failed: {e}") from e

    def run(self, user_turn: str, ctx: ToolContext, emit: Callable[[dict], None]) -> AgentResult:
        if self._loop is None:
            raise RuntimeError("Agent not loaded")
        with self._busy:
            self._ctx = ctx
            self._cancel.clear()
            if self.session_reset_after and self._turns >= self.session_reset_after:
                print(f"[AGENT] Recycling Claude Code session after {self._turns} commands")
                self._call(self._disconnect(), timeout=30)
                self._call(self._connect(), timeout=60)
            t0 = time.time()
            self._last_usage = None
            try:
                res = self._call(self._run(user_turn, ctx), timeout=self.max_seconds + 30)
            except Exception as e:
                res = AgentResult(reply="", ok=False, error=str(e) or type(e).__name__)
                self._recover()
            res.llm_ms = (time.time() - t0) * 1000
            self._turns += 1
            self._record_usage(emit)
            return res

    def _record_usage(self, emit) -> None:
        """Tokens of the last command (part of the Claude subscription: recorded without a cost)."""
        u = self._last_usage
        if self.meter is None or not isinstance(u, dict):
            return
        usage = Usage(input_tokens=int(u.get("input_tokens") or 0), output_tokens=int(u.get("output_tokens") or 0),
                      cache_read=int(u.get("cache_read_input_tokens") or 0),
                      cache_write=int(u.get("cache_creation_input_tokens") or 0))
        try:
            emit(self.meter.record(self.name, self.model, usage, dict(self.settings, kind="claude_code")))
        except Exception as e:
            print(f"[AGENT] Usage not recorded: {e}")

    async def _run(self, user_turn: str, ctx: ToolContext) -> AgentResult:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        await self._client.query(user_turn)
        queue: asyncio.Queue = asyncio.Queue()

        async def pump():
            try:
                async for msg in self._client.receive_response():
                    await queue.put(msg)
            except Exception as e:
                await queue.put(e)
            finally:
                await queue.put(None)

        pump_task = asyncio.create_task(pump())
        started = time.time()
        last_event = time.time()
        texts, calls = [], []
        result = None
        interrupted = None
        try:
            while True:
                if interrupted is None and (ctx.cancel_event.is_set() or self._cancel.is_set()
                                            or (ctx.interrupt_event is not None and ctx.interrupt_event.is_set())):
                    interrupted = "cancelled"
                    await self._safe_interrupt()
                if interrupted is None and time.time() - started > self.max_seconds:
                    interrupted = "timeout"
                    await self._safe_interrupt()
                idle_limit = self.task_step_timeout if ctx.task_id else self.step_timeout
                if (interrupted is None and self._tools_running == 0
                        and time.time() - last_event > idle_limit):
                    interrupted = "timeout"
                    await self._safe_interrupt()
                if interrupted is not None and time.time() - last_event > 15:
                    raise TimeoutError("Claude did not stop after interrupt")
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if msg is None:
                    break
                if isinstance(msg, Exception):
                    raise msg
                last_event = time.time()
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            texts.append(block.text.strip())
                        elif isinstance(block, ToolUseBlock):
                            calls.append({"tool": block.name.split("__")[-1], "args": block.input})
                elif isinstance(msg, ResultMessage):
                    result = msg
                    self._last_usage = getattr(msg, "usage", None)
        finally:
            if not pump_task.done():
                pump_task.cancel()

        if interrupted == "cancelled":
            return AgentResult(reply="", ok=False, cancelled=True, error="Cancelled", tool_calls=calls)
        if interrupted == "timeout":
            return AgentResult(reply="", ok=False, error=f"No answer from Claude within the time limit "
                               f"(llm.providers.claude_code.timeout_seconds={self.step_timeout:.0f}s idle, "
                               f"agent.max_seconds={self.max_seconds:.0f}s total)", tool_calls=calls)
        if result is None:
            return AgentResult(reply="", ok=False, error="Claude Code ended without a result", tool_calls=calls)
        turns = getattr(result, "num_turns", 0) or 0
        if result.subtype == "error_max_turns" or getattr(result, "terminal_reason", None) == "max_turns":
            reply = texts[-1] if texts else ""
            return AgentResult(reply=reply or "I stopped because the task needed more steps than I'm allowed.",
                               ok=True, tool_calls=calls, turns=turns)
        if result.is_error:
            detail = result.result or ("; ".join(map(str, result.errors)) if result.errors else "") or result.subtype
            status = getattr(result, "api_error_status", None)
            return AgentResult(reply="", ok=False, error=f"Claude Code error{f' (HTTP {status})' if status else ''}: {detail}",
                               tool_calls=calls, turns=turns)
        reply = (result.result or (texts[-1] if texts else "")).strip()
        return AgentResult(reply=reply, ok=True, tool_calls=calls, turns=turns)

    async def _safe_interrupt(self):
        try:
            await asyncio.wait_for(self._client.interrupt(), timeout=5)
        except Exception as e:
            print(f"[AGENT] Interrupt failed: {e}")

    def _recover(self):
        """After a failed or stuck turn, start a fresh session so the next command works."""
        try:
            self._call(self._disconnect(), timeout=15)
        except Exception:
            pass
        try:
            self._call(self._connect(), timeout=60)
            print("[AGENT] Claude Code session reconnected")
        except Exception as e:
            print(f"[AGENT] Reconnect failed: {e}")

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        if self._loop is None:
            return
        with self._busy:
            self._call(self._disconnect(), timeout=15)
            self._call(self._connect(), timeout=60)

    def unload(self) -> None:
        if self._loop is not None and self._client is not None:
            try:
                self._call(self._disconnect(), timeout=15)
            except Exception as e:
                print(f"[AGENT] Session close error (ignored): {e}")
        self._stop_loop()
        print("[AGENT] Claude Code session closed")
