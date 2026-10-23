"""The Claude Code agent backend with the model mocked (no network).

- Claude Code backend: MCP tool handlers route through PolicyExecutor; session options
  disable built-in tools and allow only the jarvis MCP tools.
"""
import asyncio
import unittest

from src.agent import Agent
from src.agent.claude_code_backend import ClaudeCodeAgentBackend
from src.agent.prompt import compose_user_turn
from src.memory.store import MemoryStore
from src.tools.approvals import ApprovalBroker
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec
from tests.fakes import temp_db

CFG = {"llm": {"claude": {"model": "claude-test", "effort": "low", "timeout_seconds": 5}},
       "agent": {"max_turns": 4, "memory_context_items": 5}, "tools": {"permissions": {}, "approval_timeout_ms": 1000}}


def make_executor(calls, broker=None, memory=None):
    reg = ToolRegistry()
    reg.register(ToolSpec("open_app", "Open an app", {"type": "object", "properties": {"app_name": {"type": "string", "enum": ["notepad"]}},
                                                        "required": ["app_name"]},
                          lambda a, c: calls.append(a) or ToolResult(ok=True, content="Opened: notepad (notepad.exe)")))
    reg.register(ToolSpec("danger", "Needs approval", {"type": "object", "properties": {}},
                          lambda a, c: calls.append("danger") or ToolResult(ok=True, content="did danger"), permission="ask"))
    return PolicyExecutor(reg, lambda: CFG, broker, memory)


# ── minimal stand-ins for anthropic response objects ─────────────────────────
class ClaudeCodeBackendTests(unittest.TestCase):
    def test_options_lock_down_the_session(self):
        b = ClaudeCodeAgentBackend(CFG, make_executor([]))
        opts = b._options()
        self.assertEqual(opts.tools, [])
        self.assertEqual(sorted(opts.allowed_tools), ["mcp__jarvis__danger", "mcp__jarvis__open_app"])
        self.assertTrue(opts.strict_mcp_config)
        self.assertEqual(opts.permission_mode, "dontAsk")
        self.assertEqual(opts.setting_sources, [])
        self.assertEqual(opts.max_turns, CFG["agent"]["max_turns"])
        self.assertEqual(opts.model, "claude-test")
        self.assertIn("jarvis", opts.mcp_servers)

    def test_mcp_handler_routes_through_policy(self):
        calls = []
        broker = ApprovalBroker()
        broker.prompter = lambda req: broker.resolve(req.id, False, via="test")
        b = ClaudeCodeAgentBackend(CFG, make_executor(calls, broker))
        ok = asyncio.run(b._make_handler("open_app")({"app_name": "notepad"}))
        self.assertEqual(ok, {"content": [{"type": "text", "text": "Opened: notepad (notepad.exe)"}], "is_error": False})
        bad = asyncio.run(b._make_handler("open_app")({"app_name": "rm"}))
        self.assertTrue(bad["is_error"])
        denied = asyncio.run(b._make_handler("danger")({}))
        self.assertTrue(denied["is_error"])
        self.assertIn("DENIED", denied["content"][0]["text"])
        self.assertEqual(calls, [{"app_name": "notepad"}])


class AgentFacadeTests(unittest.TestCase):
    def test_memories_are_injected_into_the_turn(self):
        mem = MemoryStore(temp_db())
        self.addCleanup(mem.close)
        mem.add_memory("The user's dog is called Bruno", "fact")
        seen = []

        class Backend:
            name, model = "fake", "m"

            def run(self, turn, ctx, emit):
                seen.append(turn)
                from src.agent.base import AgentResult
                return AgentResult(reply="ok")

        Agent(Backend(), mem, CFG).handle("what is my dog called", "voice", ToolContext(), lambda e: None)
        self.assertIn("Bruno", seen[0])
        self.assertIn("voice transcript", seen[0])
        self.assertTrue(seen[0].rstrip().endswith("what is my dog called"))

    def test_compose_user_turn_without_memories(self):
        turn = compose_user_turn("hi", "text", [])
        self.assertIn("typed", turn)
        self.assertNotIn("remember about", turn)


if __name__ == "__main__":
    unittest.main()
