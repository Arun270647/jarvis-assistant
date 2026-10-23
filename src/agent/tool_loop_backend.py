"""The agent for API providers (Anthropic API, OpenAI-compatible servers): a generic tool-calling loop.

    run(turn): append the user turn; then up to agent.max_turns times: ask the model (system prompt +
    history + tool definitions); no tool calls -> that's the reply; otherwise run each call through
    PolicyExecutor.execute() (one at a time, so approvals never overlap) and append the results.

It keeps what the Claude Code backend guarantees: every call is validated, permission-checked, approved and
logged by the policy executor; cancel (and the task pause interrupt) stops the request in flight;
agent.max_seconds bounds the whole request and the provider's timeout_seconds each wait for the model;
reset() forgets the conversation; untrusted tool output stays wrapped; screenshots reach models that can
see and become a text note for those that can't. The task orchestrator's continue prompts are ordinary
run() calls.

The conversation only ever grows (some providers bind signed reasoning to the exact history, and it keeps
prompt caching effective). When the last request read more than history_budget_tokens, the next turn
starts from a summary written by the utility model instead (or a short note when that fails).
"""
import json
import threading
import time
from typing import Callable, Optional

from src.agent.base import AgentBackend, AgentResult
from src.agent.prompt import build_agent_system_prompt
from src.core.config_store import assistant_name
from src.agent.providers.base import Message, ProviderError, ToolDef, ToolResultItem
from src.agent.toolsets import tool_names
from src.agent.usage import budget_message
from src.tools.registry import ToolContext, ToolResult

STARTUP_PROMPT = "[startup check] Reply with exactly: ready"

SUMMARY_PROMPT = """Below is the conversation so far between a user and Jarvis, their voice assistant on a \
Windows PC. It is being shortened to save space, and your summary replaces it in Jarvis's memory. \
Write the summary for Jarvis: what the user asked for, what Jarvis did (tool actions and their real \
results), decisions and facts the user stated, open tasks and anything still waiting for an answer. \
Plain text, at most 300 words, no preamble.

{transcript}"""


def _profile() -> dict:
    try:
        from src.core.config_store import load_profile
        return load_profile()
    except Exception:
        return {}


class _AnyEvent:
    """is_set() when any of the events is set (cancel, the backend's own cancel, the task interrupt)."""

    def __init__(self, *events):
        self.events = [e for e in events if e is not None]

    def is_set(self) -> bool:
        return any(e.is_set() for e in self.events)


class ToolLoopBackend(AgentBackend):
    def __init__(self, config: dict, executor, adapter, summarizer: Optional[Callable[[str], str]] = None,
                 meter=None):
        acfg = config.get("agent", {}) or {}
        self.adapter = adapter
        self.settings = adapter.settings
        self.name = adapter.provider
        self.model = adapter.model
        self.label = self.settings.get("label") or adapter.provider
        self.executor = executor
        self.summarizer = summarizer
        self.meter = meter
        self.max_turns = int(acfg.get("max_turns", 8))
        self.max_seconds = float(acfg.get("max_seconds", 600))
        self.request_timeout = float(self.settings.get("timeout_seconds", 120))
        self.task_timeout = max(self.request_timeout, float((acfg.get("task") or {}).get("idle_timeout_seconds", 180)))
        self.history_budget = int(self.settings.get("history_budget_tokens", 60000))
        self.warmup = bool((config.get("llm", {}) or {}).get("warmup", True))
        self.tool_names = tool_names(executor.registry, config, self.settings)
        offered = set(self.tool_names)
        self.tools = [ToolDef(s.name, s.description, s.input_schema)
                      for s in executor.registry.specs() if s.name in offered]
        coding_cli = (config.get("coding_agent", {}) or {}).get("cli", "claude")
        self.system_prompt = build_agent_system_prompt(self.tool_names, _profile(), bool(acfg.get("full_access")),
                                                       coding_cli=coding_cli, name=assistant_name(config))
        self.history: list = []
        self._last_prompt_tokens = 0
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._loaded = False

    # ------------------------------------------------------------ lifecycle

    def load(self) -> None:
        print(f"[AGENT] Connecting to {self.label} ({self.model}, {len(self.tools)} tools)...")
        t0 = time.time()
        if self.warmup:
            # Checks the key and model, loads a local model into memory and fills the provider's prompt cache.
            try:
                comp = self.adapter.complete(self.system_prompt, [Message("user", text=STARTUP_PROMPT)], self.tools,
                                             threading.Event(), self.request_timeout, total_s=max(60.0, self.request_timeout * 2))
            except ProviderError as e:
                raise RuntimeError(f"{self.label} is not working: {e}") from e
            self._record(comp.usage, emit=None)
            print(f"[AGENT] {self.label} answered in {time.time() - t0:.2f}s: {comp.text[:40]!r}")
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False
        self.adapter.close()
        print(f"[AGENT] {self.label} session closed")

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        with self._busy:
            self.history = []
            self._last_prompt_tokens = 0

    # ------------------------------------------------------------ a request

    def run(self, user_turn: str, ctx: ToolContext, emit: Callable[[dict], None]) -> AgentResult:
        if not self._loaded:
            raise RuntimeError("Agent not loaded")
        with self._busy:
            self._cancel.clear()
            t0 = time.time()
            try:
                res = self._run(user_turn, ctx, emit, t0)
            except Exception as e:                      # a bug must not leave the pipeline without an answer
                res = AgentResult(reply="", ok=False, error=f"{type(e).__name__}: {e}")
            res.llm_ms = (time.time() - t0) * 1000
            return res

    def _run(self, user_turn: str, ctx: ToolContext, emit, started: float) -> AgentResult:
        limit = self.meter.over_budget() if self.meter is not None else None
        if limit is not None:
            return self._over_budget(limit, [], 0)
        self._maybe_compact()
        self.history.append(Message("user", text=user_turn))
        stop = _AnyEvent(ctx.cancel_event, self._cancel, ctx.interrupt_event)
        calls_done, last_text, turns, retried = [], "", 0, set()
        while turns < self.max_turns:
            if stop.is_set():
                return self._cancelled(calls_done, turns)
            remaining = self.max_seconds - (time.time() - started)
            if remaining <= 0:
                return self._timeout(calls_done, turns)
            if turns and self.meter is not None and (limit := self.meter.over_budget()) is not None:
                return self._over_budget(limit, calls_done, turns)
            try:
                comp = self.adapter.complete(self.system_prompt, self.history, self.tools, stop,
                                             self.task_timeout if ctx.task_id else self.request_timeout,
                                             total_s=remaining)
            except ProviderError as e:
                if e.kind == "cancelled":
                    return self._cancelled(calls_done, turns)
                if e.kind == "timeout" and time.time() - started >= self.max_seconds:
                    return self._timeout(calls_done, turns)
                if e.kind == "no_vision" and "no_vision" not in retried:
                    retried.add("no_vision")
                    self._drop_images()
                    continue
                return AgentResult(reply="", ok=False, error=f"{self.label}: {e}", spoken=e.spoken(),
                                   tool_calls=calls_done, turns=turns)
            turns += 1
            self._last_prompt_tokens = comp.usage.prompt_tokens or self._estimate_tokens()
            self._record(comp.usage, emit)
            self.history.append(Message("assistant", text=comp.text, tool_calls=list(comp.tool_calls), raw=comp.raw,
                                        raw_from=self.adapter.kind))
            last_text = comp.text or last_text
            if not comp.tool_calls:
                reply = comp.text
                if comp.stop_reason == "refusal":
                    reply = reply or "I can't help with that one."
                elif comp.stop_reason == "max_tokens" and not reply:
                    reply = "My answer got cut off before I could finish. Try asking for something shorter."
                return AgentResult(reply=reply, ok=True, tool_calls=calls_done, turns=turns)
            results, stopped = self._run_tools(comp.tool_calls, ctx, stop, calls_done)
            self.history.append(Message("tool_results", results=results))
            if stopped:
                return self._cancelled(calls_done, turns)
        return AgentResult(reply=last_text or "I stopped because the task needed more steps than I'm allowed.",
                           ok=True, tool_calls=calls_done, turns=turns)

    def _run_tools(self, calls: list, ctx: ToolContext, stop: _AnyEvent, calls_done: list):
        """Run the calls one by one. Every call gets a result (the history must answer each call), also the
        ones skipped because the user stopped. Returns (results, stopped)."""
        offered = set(self.tool_names)
        vision = self.adapter.capabilities.vision
        results, stopped = [], False
        for call in calls:
            if stopped or stop.is_set():
                stopped = True
                results.append(ToolResultItem(call.id, call.name, "CANCELLED: the user stopped this request before "
                                              "the call ran.", is_error=True))
                continue
            calls_done.append({"tool": call.name, "args": call.args})
            images = []
            if call.error:
                text, is_error = (f"INVALID ARGUMENTS: {call.error}. Call {call.name} again with a JSON object that "
                                  "matches its schema."), True
            elif call.name not in offered:
                text, is_error = (f"ERROR: there is no tool called {call.name!r} available to you. Use only the tools "
                                  "you were given."), True
            else:
                try:
                    result = self.executor.execute(call.name, dict(call.args), ctx)
                except Exception as e:      # the call must still get a result, or every later request fails
                    result = ToolResult(ok=False, content=f"{type(e).__name__}: {e}", status="failed")
                text, is_error = result.as_text(), not result.ok
                images = list(result.data.get("images") or [])
                if images and not vision:
                    text += (f"\n(The tool also returned {len(images)} image(s), but this model can't see images; "
                             "work from the text above.)")
                    images = []
                if result.status in ("cancelled", "paused") and stop.is_set():
                    stopped = True
            results.append(ToolResultItem(call.id, call.name, text, is_error=is_error, images=images))
        return results, stopped

    # ------------------------------------------------------------ results

    def _cancelled(self, calls, turns) -> AgentResult:
        return AgentResult(reply="", ok=False, cancelled=True, error="Cancelled", tool_calls=calls, turns=turns)

    def _timeout(self, calls, turns) -> AgentResult:
        return AgentResult(reply="", ok=False, error=f"No answer from {self.label} within the time limit "
                           f"(agent.max_seconds={self.max_seconds:.0f}s)", spoken="That took too long, so I stopped.",
                           tool_calls=calls, turns=turns)

    def _over_budget(self, limit: float, calls, turns) -> AgentResult:
        return AgentResult(reply="", ok=False, error=f"Daily AI budget of ${limit:.2f} reached (llm.daily_budget_usd)",
                           spoken=budget_message(limit), stage="budget", tool_calls=calls, turns=turns)

    def _record(self, usage, emit) -> None:
        if self.meter is None:
            return
        ev = self.meter.record(self.name, self.model, usage, self.settings)
        if emit is not None:
            try:
                emit(ev)
            except Exception:
                pass

    # ------------------------------------------------------------ history

    def _drop_images(self) -> None:
        """The server refused images: treat the model as text-only from now on."""
        print(f"[AGENT] {self.model} rejected an image; continuing without images")
        self.adapter.capabilities.vision = False
        for m in self.history:
            for r in m.results:
                if r.images:
                    r.text += f"\n({len(r.images)} image(s) removed: this model can't see images.)"
                    r.images = []

    def _estimate_tokens(self) -> int:
        chars = len(self.system_prompt) + sum(len(json.dumps(t.schema)) + len(t.description) for t in self.tools)
        for m in self.history:
            chars += len(m.text) + sum(len(json.dumps(c.args)) for c in m.tool_calls) + sum(len(r.text) for r in m.results)
        return chars // 4

    def _maybe_compact(self) -> None:
        if not self.history or self._last_prompt_tokens < self.history_budget:
            return
        before = self._last_prompt_tokens
        summary = ""
        if self.summarizer is not None:
            try:
                summary = (self.summarizer(SUMMARY_PROMPT.format(transcript=self._transcript())) or "").strip()
            except Exception as e:
                print(f"[AGENT] Could not summarize the conversation: {e}")
        note = ("[context] The earlier conversation, summarized to save space:\n" + summary if summary else
                "[context] The earlier conversation was cleared to save space. Ask the user if you need something "
                "from it.")
        self.history = [Message("user", text=note)]
        self._last_prompt_tokens = 0
        print(f"[AGENT] Conversation compacted at about {before} tokens ({'summary' if summary else 'cleared'})")

    def _transcript(self, max_chars: int = 60000) -> str:
        lines = []
        for m in self.history:
            if m.role == "user":
                lines.append(f"USER: {m.text[:3000]}")
            elif m.role == "assistant":
                if m.text:
                    lines.append(f"JARVIS: {m.text[:3000]}")
                for c in m.tool_calls:
                    lines.append(f"JARVIS CALLED {c.name} {json.dumps(c.args, ensure_ascii=False)[:300]}")
            else:
                for r in m.results:
                    lines.append(f"RESULT OF {r.name}: {r.text[:400]}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else "(earlier part omitted)\n" + text[-max_chars:]
