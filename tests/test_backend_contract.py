"""One contract, every backend: the same scripted conversations must behave the same on

  - ToolLoopBackend with a scripted adapter,
  - ToolLoopBackend + OpenAICompatAdapter over HTTP (fake server),
  - ToolLoopBackend + AnthropicAdapter over HTTP (fake server, streamed),
  - ClaudeCodeAgentBackend with a fake Claude Agent SDK client (no CLI).

A script is a list of model steps: ("text", reply) or ("tools", [(name, args), ...]). Every backend must
answer with the reply, run tools only through the PolicyExecutor (validation, approvals), report denied
and invalid calls back to the model, and stop when cancelled.
"""
import unittest

from src.agent.claude_code_backend import ClaudeCodeAgentBackend
from src.agent.providers.anthropic_api import AnthropicAdapter
from src.agent.providers.base import Completion, ToolCall, Usage
from src.agent.providers.openai_compat import OpenAICompatAdapter
from src.agent.tool_loop_backend import ToolLoopBackend
from src.memory.store import MemoryStore
from src.tools.approvals import ApprovalBroker
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec
from tests import fake_llm as F
from tests.fakes import temp_db
from tests.test_tool_loop import ScriptedAdapter

CFG = {"llm": {"warmup": False, "main": {"provider": "claude_code"},
               "providers": {"claude_code": {"kind": "claude_code", "model": "claude-test", "effort": "low",
                                             "timeout_seconds": 5}}},
       "agent": {"max_turns": 6, "max_seconds": 30, "memory_context_items": 0},
       "tools": {"permissions": {}, "approval_timeout_ms": 1000}}


class Contract:
    """Mixin. Subclasses build a loaded backend for a script and say which tool results the model saw."""

    def make(self, script):
        raise NotImplementedError

    def results_seen(self) -> list:
        raise NotImplementedError

    def setUp(self):
        self.calls = []
        self.memory = MemoryStore(temp_db())
        self.addCleanup(self.memory.close)
        broker = ApprovalBroker()
        broker.prompter = lambda req: broker.resolve(req.id, False, via="test")
        reg = ToolRegistry()
        reg.register(ToolSpec("open_app", "Open an app", {"type": "object", "properties": {
            "app_name": {"type": "string", "enum": ["notepad"]}}, "required": ["app_name"]},
            lambda a, c: self.calls.append(a) or ToolResult(ok=True, content="Opened: notepad (notepad.exe)")))
        reg.register(ToolSpec("danger", "Needs approval", {"type": "object", "properties": {}},
                              lambda a, c: self.calls.append("danger") or ToolResult(ok=True, content="did danger"),
                              permission="ask"))
        self.executor = PolicyExecutor(reg, lambda: CFG, broker, self.memory)

    def run_turn(self, backend, turn="hello", ctx=None):
        return backend.run(turn, ctx or ToolContext(session_id="contract"), lambda ev: None)

    def test_plain_answer(self):
        res = self.run_turn(self.make([("text", "Hello from the model.")]))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.reply, "Hello from the model.")
        self.assertEqual(res.tool_calls, [])

    def test_tool_then_answer(self):
        res = self.run_turn(self.make([("tools", [("open_app", {"app_name": "notepad"})]), ("text", "Notepad is open.")]))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.reply, "Notepad is open.")
        self.assertEqual([c["tool"] for c in res.tool_calls], ["open_app"])
        self.assertEqual(self.calls, [{"app_name": "notepad"}])
        self.assertEqual(self.results_seen(), ["Opened: notepad (notepad.exe)"])

    def test_denied_tool_is_reported_to_the_model(self):
        res = self.run_turn(self.make([("tools", [("danger", {})]), ("text", "You declined.")]))
        self.assertEqual(res.reply, "You declined.")
        self.assertEqual(self.calls, [])
        self.assertTrue(self.results_seen()[0].startswith("DENIED"))

    def test_invalid_arguments_never_reach_the_tool(self):
        res = self.run_turn(self.make([("tools", [("open_app", {"app_name": "rm -rf"})]), ("text", "That failed.")]))
        self.assertEqual(res.reply, "That failed.")
        self.assertEqual(self.calls, [])
        self.assertTrue(self.results_seen()[0].startswith("INVALID ARGUMENTS"))

    def test_cancelled_before_it_starts(self):
        ctx = ToolContext()
        ctx.cancel_event.set()
        res = self.run_turn(self.make([("tools", [("open_app", {"app_name": "notepad"})]), ("text", "never")]), ctx=ctx)
        self.assertTrue(res.cancelled)
        self.assertEqual(self.calls, [])


# ---------------------------------------------------------------------- ToolLoopBackend, scripted

class ScriptedLoopContract(Contract, unittest.TestCase):
    def make(self, script):
        steps = []
        for kind, payload in script:
            if kind == "text":
                steps.append(Completion(text=payload, tool_calls=[], stop_reason="end", usage=Usage()))
            else:
                steps.append(Completion(text="", stop_reason="tool_use", usage=Usage(), tool_calls=[
                    ToolCall(id=f"c{i}", name=n, args=a) for i, (n, a) in enumerate(payload)]))
        self.adapter = ScriptedAdapter(steps)
        backend = ToolLoopBackend(CFG, self.executor, self.adapter)
        backend.load()
        return backend

    def results_seen(self):
        return [r.text for m in self.adapter.seen[-1]["messages"] if m.role == "tool_results" for r in m.results]


# ---------------------------------------------------------------------- ToolLoopBackend over HTTP

class OpenAIHttpContract(Contract, unittest.TestCase):
    def make(self, script):
        self.server = F.FakeLLMServer()
        self.addCleanup(self.server.close)
        for kind, payload in script:
            self.server.push(F.openai_text(payload) if kind == "text" else
                             F.openai_tools(*[(f"call_{i}", n, a) for i, (n, a) in enumerate(payload)]))
        adapter = OpenAICompatAdapter("custom", "model-a", {"base_url": self.server.url + "/v1", "label": "Fake"}, "k")
        backend = ToolLoopBackend(CFG, self.executor, adapter)
        backend.load()
        self.addCleanup(backend.unload)
        return backend

    def results_seen(self):
        return [m["content"] for m in self.server.bodies("/chat/completions")[-1]["messages"] if m["role"] == "tool"]


class AnthropicHttpContract(Contract, unittest.TestCase):
    def make(self, script):
        self.server = F.FakeLLMServer()
        self.addCleanup(self.server.close)
        for kind, payload in script:
            self.server.push(F.anthropic_text(payload) if kind == "text" else
                             F.anthropic_tools(*[(f"toolu_{i}", n, a) for i, (n, a) in enumerate(payload)]))
        adapter = AnthropicAdapter("anthropic", "claude-test", {"base_url": self.server.url, "max_tokens": 500}, "k")
        backend = ToolLoopBackend(CFG, self.executor, adapter)
        backend.load()
        self.addCleanup(backend.unload)
        return backend

    def results_seen(self):
        last = self.server.bodies("/v1/messages")[-1]["messages"]
        return [b["content"][0]["text"] for m in last if m["role"] == "user" and isinstance(m["content"], list)
                for b in m["content"] if b.get("type") == "tool_result"]


# ---------------------------------------------------------------------- Claude Code backend, fake SDK client

class FakeSDKClient:
    """Plays a script like the Claude Code CLI would: tool_use messages, the MCP handler runs, then a result."""

    def __init__(self, backend, script):
        self.backend = backend
        self.script = list(script)
        self.results = []
        self.interrupted = False

    async def query(self, prompt):
        self.prompt = prompt

    async def receive_response(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
        turns = 0
        for kind, payload in self.script:
            if self.interrupted:
                return
            turns += 1
            if kind == "tools":
                yield AssistantMessage(content=[ToolUseBlock(id=f"t{i}", name=f"mcp__jarvis__{n}", input=a)
                                                for i, (n, a) in enumerate(payload)], model="claude-test")
                for name, args in payload:
                    out = await self.backend._make_handler(name)(args)
                    self.results.append(out["content"][0]["text"])
            else:
                yield AssistantMessage(content=[TextBlock(text=payload)], model="claude-test")
                yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=turns,
                                    session_id="s", result=payload, usage={"input_tokens": 10, "output_tokens": 3})
                return

    async def interrupt(self):
        self.interrupted = True

    async def disconnect(self):
        pass


class ClaudeCodeContract(Contract, unittest.TestCase):
    def make(self, script):
        backend = ClaudeCodeAgentBackend(CFG, self.executor)
        backend._start_loop()
        self.addCleanup(backend._stop_loop)
        self.client = FakeSDKClient(backend, script)
        backend._client = self.client
        return backend

    def results_seen(self):
        return self.client.results


if __name__ == "__main__":
    unittest.main()
