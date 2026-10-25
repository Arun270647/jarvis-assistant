"""Wiring around the providers: which backend create_agent builds, the utility role, tool profiles, usage
accounting and the coding agent behind delegate_to_coding_agent."""
import subprocess
import unittest
from unittest import mock

from src.agent import create_agent
from src.agent.claude_code_backend import ClaudeCodeAgentBackend
from src.agent.providers.base import Usage
from src.agent.tool_loop_backend import ToolLoopBackend
from src.agent.toolsets import CORE_TOOLS, profile_for, tool_names
from src.agent.usage import UsageMeter, cost_usd, price_for
from src.agent.utility import utility_generator
from src.core import config_store
from src.memory.store import MemoryStore
from src.router.action_router import coding_agent_command
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolRegistry, ToolResult, ToolSpec
from tests import fake_llm as F
from tests.fakes import temp_db


def shipped(**main):
    cfg = config_store.defaults()
    cfg["llm"]["warmup"] = False
    for provider, settings in main.items():
        cfg["llm"]["main"]["provider"] = provider
        cfg["llm"]["providers"][provider].update(settings)
    return cfg


def tiny_executor(cfg):
    reg = ToolRegistry()
    for name in ("open_app", "get_datetime", "run_command"):
        reg.register(ToolSpec(name, name, {"type": "object", "properties": {}}, lambda a, c: ToolResult(ok=True, content="ok")))
    return PolicyExecutor(reg, lambda: cfg, None, None)


class CreateAgentTests(unittest.TestCase):
    def test_claude_is_the_default(self):
        cfg = shipped()
        agent = create_agent(cfg, tiny_executor(cfg))
        self.assertIsInstance(agent.backend, ClaudeCodeAgentBackend)
        self.assertEqual((agent.name, agent.model), ("claude_code", "claude-sonnet-5-5"))

    def test_an_api_provider_gets_the_tool_loop(self):
        cfg = shipped(ollama={"model": "qwen3:8b"})
        agent = create_agent(cfg, tiny_executor(cfg))
        self.assertIsInstance(agent.backend, ToolLoopBackend)
        self.assertEqual((agent.name, agent.model), ("ollama", "qwen3:8b"))
        self.assertEqual(agent.backend.tool_names, ["open_app", "get_datetime"], "local: the core tools")

    def test_missing_key_or_model_is_refused_up_front(self):
        from src.core import credentials
        with mock.patch.object(credentials, "read", lambda t: None), mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            cfg = shipped(openai={"model": "some-model"})
            with self.assertRaises(Exception) as cm:
                create_agent(cfg, tiny_executor(cfg))
            self.assertIn("no API key", str(cm.exception))
        cfg = shipped(ollama={"model": ""})
        with self.assertRaises(Exception):
            create_agent(cfg, tiny_executor(cfg))


class ToolProfileTests(unittest.TestCase):
    def test_profiles(self):
        reg = ToolRegistry()
        for name in ("open_app", "run_command", "desktop_click", "recall"):
            reg.register(ToolSpec(name, name, {"type": "object", "properties": {}}, lambda a, c: None))
        cfg = {"tools": {"profile": "auto"}}
        self.assertEqual(profile_for(cfg, {"local": True}), "core")
        self.assertEqual(profile_for(cfg, {}), "full")
        self.assertEqual(profile_for(cfg, {"tool_profile": "core"}), "core")
        self.assertEqual(tool_names(reg, cfg, {}), ["open_app", "run_command", "desktop_click", "recall"])
        self.assertEqual(tool_names(reg, cfg, {"local": True}), ["open_app", "recall"])
        self.assertEqual(tool_names(reg, {"tools": {"profile": "custom", "custom_list": ["run_command"]}}, {}), ["run_command"])
        self.assertTrue({"create_plan", "finish_task", "read_file"} <= set(CORE_TOOLS))
        self.assertFalse({"run_command", "write_file", "desktop_click", "send_email"} & set(CORE_TOOLS))


class UtilityTests(unittest.TestCase):
    def test_claude_code_uses_print_mode_on_the_small_model(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, '[{"text": "Hi", "slot": "any"}]', "")
        with mock.patch("src.core.winproc.run_hidden", fake_run):
            out = utility_generator(shipped())("write greetings")
        self.assertEqual(seen["cmd"], ["claude", "-p", "write greetings", "--model", "claude-haiku-5-5"])
        self.assertIn("Hi", out)

    def test_an_api_provider_answers_through_its_adapter_and_records_usage(self):
        server = F.FakeLLMServer()
        self.addCleanup(server.close)
        server.push(F.openai_text("Summary text", prompt_tokens=30, completion_tokens=5))
        cfg = shipped(custom={"base_url": server.url + "/v1", "model": "big", "utility_model": "small"})
        memory = MemoryStore(temp_db())
        self.addCleanup(memory.close)
        out = utility_generator(cfg, UsageMeter(memory, lambda: cfg))("summarize")
        self.assertEqual(out, "Summary text")
        body = server.bodies()[0]
        self.assertEqual(body["model"], "small")
        self.assertNotIn("tools", body)
        self.assertEqual(memory.usage_by_day(1)[0]["requests"], 1)

    def test_greetings_default_to_the_utility_model(self):
        from src.agent.greetings import GreetingPool
        with mock.patch("src.agent.utility.utility_generator", return_value=lambda p: "[]") as gen:
            GreetingPool(shipped(), None).generator("x")
        gen.assert_called_once()


class UsageTests(unittest.TestCase):
    def test_prices_and_costs(self):
        cfg = config_store.defaults()
        p = price_for(cfg, {"kind": "anthropic"}, "claude-sonnet-5-5")
        self.assertEqual((p["input"], p["output"], p["cache_read"]), (2.0, 10.0, 0.2))
        self.assertAlmostEqual(cost_usd(Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read=1_000_000), p),
                               2.0 + 1.0 + 0.2)
        self.assertIsNone(price_for(cfg, {"kind": "openai_compat"}, "unknown-model"))
        self.assertEqual(price_for(cfg, {"local": True}, "anything")["input"], 0.0)
        self.assertEqual(price_for(cfg, {"kind": "claude_code"}, "claude-opus-5-5")["output"], 0.0)

    def test_daily_totals_and_budget(self):
        memory = MemoryStore(temp_db())
        self.addCleanup(memory.close)
        cfg = {"llm": {"daily_budget_usd": 0.5, "prices": {"m": {"input": 100.0, "output": 100.0}}}}
        meter = UsageMeter(memory, lambda: cfg)
        self.assertIsNone(meter.over_budget())
        ev = meter.record("openai", "m", Usage(input_tokens=3000, output_tokens=2000), {"kind": "openai_compat"})
        self.assertAlmostEqual(ev["cost_usd"], 0.5)
        meter.record("openai", "unpriced", Usage(input_tokens=10), {"kind": "openai_compat"})
        self.assertEqual(meter.over_budget(), 0.5)
        day = memory.usage_by_day(7)[0]
        self.assertEqual((day["requests"], day["unpriced"], round(day["cost_usd"], 3)), (2, 1, 0.5))
        cfg["llm"]["daily_budget_usd"] = None
        self.assertIsNone(meter.over_budget())


class CodingAgentTests(unittest.TestCase):
    def test_presets(self):
        cfg = config_store.defaults()
        self.assertEqual(coding_agent_command(cfg), ("Claude Code", ["claude", "-p"]))
        cfg["coding_agent"]["cli"] = "codex"
        self.assertEqual(coding_agent_command(cfg)[1], ["codex", "exec"])
        cfg["coding_agent"]["cli"] = "aider"
        self.assertEqual(coding_agent_command(cfg)[1][-1], "--message")
        cfg["coding_agent"].update(cli="custom", custom_command=["mytool", "--task"])
        self.assertEqual(coding_agent_command(cfg)[1], ["mytool", "--task"])
        for broken in ({"cli": "off"}, {"cli": "custom", "custom_command": []}):
            cfg["coding_agent"].update(broken)
            with self.assertRaises(ValueError):
                coding_agent_command(cfg)

    def test_the_tool_follows_the_setting_and_names_the_agent(self):
        from src.router.action_router import ActionRouter
        from src.tools.registry import build_default_registry
        router = ActionRouter()
        cfg = config_store.load_config()
        cfg["coding_agent"]["cli"] = "codex"
        self.assertIsNone(build_default_registry(router, None, cfg).get("delegate_to_coding_agent"),
                          "its group is off on a new install")
        cfg["tools"]["groups"]["coding_agent"] = True
        reg = build_default_registry(router, None, cfg)
        spec = reg.get("delegate_to_coding_agent")
        self.assertIn("Codex", spec.description)
        self.assertIn("codex exec", spec.description)
        cfg["coding_agent"]["cli"] = "off"
        self.assertIsNone(build_default_registry(router, None, cfg).get("delegate_to_coding_agent"))
        self.assertIsNone(reg.get("delegate_to_claude"))

    def test_a_run_uses_the_configured_command_and_keeps_output_out_of_the_repo(self):
        import tempfile
        from pathlib import Path
        from src.core import paths
        from src.router import action_router
        project = Path(tempfile.mkdtemp())
        cfg = config_store.load_config()
        import sys
        echo = [sys.executable, "-c", "import sys; print(sys.argv[-1])"]       # works on every OS
        cfg["coding_agent"].update(cli="custom", custom_command=echo, timeout_seconds=30)
        detail = action_router._delegate_to_coding_agent("all done", "demo", str(project), cfg)
        self.assertIn("The coding agent completed", detail)
        self.assertIn("all done", detail)
        self.assertEqual(list(project.iterdir()), [], "nothing written into the project")
        self.assertTrue(any((paths.cache_dir() / "coding_agent_outputs" / "demo").iterdir()))


if __name__ == "__main__":
    unittest.main()
