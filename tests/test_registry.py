"""The default registry: schemas, router-backed tools in dry-run, confirmation rules."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema

from src.core.config_store import load_config
from src.memory.store import MemoryStore
from src.router.action_router import ActionRouter
from src.tools.groups import groups_setting
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, build_default_registry
from tests.fakes import temp_db


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        cls.cfg["dry_run"] = True
        cls.cfg["tools"]["groups"] = groups_setting("full")      # every tool (test_security covers the defaults)
        cls.chrome_dir = Path(tempfile.mkdtemp(prefix="jarvis-chrome-"))     # open_chrome needs a profile, not this PC's
        (cls.chrome_dir / "Local State").write_text(json.dumps(
            {"profile": {"info_cache": {"Default": {"name": "Person 1"}}, "last_used": "Default"}}), encoding="utf-8")
        cls.cfg.setdefault("chrome", {})["user_data_dir"] = str(cls.chrome_dir)
        cls.router = ActionRouter()
        cls.router.dry_run = True
        cls.memory = MemoryStore(temp_db())
        cls.reg = build_default_registry(cls.router, cls.memory, cls.cfg)
        cls.ex = PolicyExecutor(cls.reg, lambda: cls.cfg, None, cls.memory)

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.chrome_dir, ignore_errors=True)

    def test_expected_tools(self):
        names = set(self.reg.names())
        for n in ("open_app", "media_control", "system_command", "open_chrome", "read_webpage", "search_web", "get_datetime", "remember", "recall", "forget"):
            self.assertIn(n, names)
        if self.cfg.get("coding_agent", {}).get("cli", "claude") != "off":
            self.assertIn("delegate_to_coding_agent", names)

    def test_every_schema_is_valid_json_schema_and_closed(self):
        for spec in self.reg.specs():
            jsonschema.Draft202012Validator.check_schema(spec.input_schema)
            self.assertFalse(spec.input_schema.get("additionalProperties", True), spec.name)
            self.assertTrue(spec.description)

    def test_app_enum_comes_from_profile(self):
        apps = set(self.router.profile["apps"])
        self.assertEqual(set(self.reg.get("open_app").input_schema["properties"]["app_name"]["enum"]), apps)

    def test_router_tools_dry_run_execute_nothing(self):
        app = sorted(self.router.profile["apps"])[0]
        r = self.ex.execute("open_app", {"app_name": app}, ToolContext())
        self.assertTrue(r.ok)
        self.assertEqual(r.status, "dry_run")
        self.assertTrue(r.dry_run)
        r = self.ex.execute("media_control", {"control": "mute"}, ToolContext())
        self.assertEqual(r.status, "dry_run")
        r = self.ex.execute("open_chrome", {"search": "a&b c"}, ToolContext())
        self.assertEqual(r.status, "dry_run")

    def test_profile_confirm_commands_require_approval(self):
        for key, mapping in self.router.profile["system"].items():
            perm = self.ex.permission_for("system_command", {"command": key})
            self.assertEqual(perm, "ask" if mapping.get("confirm") else "allow", key)

    def test_confirmed_system_command_without_broker_is_denied(self):
        confirm = [k for k, v in self.router.profile["system"].items() if v.get("confirm")]
        if not confirm:
            self.skipTest("no confirm-marked system command in profile")
        r = self.ex.execute("system_command", {"command": confirm[0]}, ToolContext())
        self.assertEqual(r.status, "denied")

    def test_delegate_asks_when_required(self):
        if "delegate_to_coding_agent" not in self.reg.names():
            self.skipTest("delegation disabled")
        expected = "ask" if self.cfg["coding_agent"].get("require_confirmation", True) else "allow"
        self.assertEqual(self.ex.permission_for("delegate_to_coding_agent", {"task": "x"}), expected)

    def test_memory_tools_round_trip(self):
        r = self.ex.execute("remember", {"content": "My printer is the HP in the hall", "kind": "fact"}, ToolContext())
        self.assertTrue(r.ok)
        r = self.ex.execute("recall", {"query": "printer"}, ToolContext())
        self.assertIn("HP in the hall", r.content)

    def test_get_datetime(self):
        r = self.ex.execute("get_datetime", {}, ToolContext())
        self.assertTrue(r.ok)
        self.assertRegex(r.content, r"\d{4}")


if __name__ == "__main__":
    unittest.main()
