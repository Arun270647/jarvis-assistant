"""Phase 4 safeguards: tool groups, the untrusted-content rule, approvals that need a click or key, redaction
of keys in logs and events, the desktop block list, and a static check that credentials are never logged."""
import ast
import contextlib
import io
import json
import logging
import threading
import unittest
from pathlib import Path
from unittest import mock

from src.core import config_store
from src.core.redact import MASK, redact
from src.memory.store import MemoryStore
from src.router.action_router import ActionRouter
from src.tools import groups
from src.tools.approvals import ApprovalBroker, ApprovalRequest
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec, build_default_registry
from tests.fakes import temp_db

ROOT = Path(__file__).resolve().parents[1]
KEYS = {
    "anthropic": "sk-ant-api03-AbCdEf0123456789AbCdEf0123456789",  # export-scan: ignore (fake keys)
    "openai project": "sk-proj-Ab3dEf_Gh1jKlMnOpQrStUvWx-yz012345",  # export-scan: ignore (fake keys)
    "openai service account": "sk-svcacct-Ab3dEf_Gh1jKlMnOpQrStUvWx012345",  # export-scan: ignore (fake keys)
    "openrouter": "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",  # export-scan: ignore (fake keys)
    "groq": "gsk_aB3dEfGhIjKlMnOpQrStUvWx0123456789",  # export-scan: ignore (fake keys)
    "xai": "xai-aB3dEfGhIjKlMnOpQrStUvWx0123456789",  # export-scan: ignore (fake keys)
    "hugging face": "hf_aB3dEfGhIjKlMnOpQrStUvWx01234567",  # export-scan: ignore (fake keys)
    "google": "AIzaSyA0123456789abcdefghijklmnopqrstu",  # export-scan: ignore (fake keys)
    "github": "ghp_0123456789abcdefghijABCDEFGHIJ012345",  # export-scan: ignore (fake keys)
}


def all_groups(cfg: dict) -> dict:
    cfg.setdefault("tools", {})["groups"] = groups.groups_setting("full")
    return cfg


class ToolGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = ActionRouter()
        cls.memory = MemoryStore(temp_db())
        cls.cfg = config_store.load_config()

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()

    def full_registry(self):
        from src.agent.tasks import TaskManager
        cfg = all_groups(json.loads(json.dumps(self.cfg)))
        reg = build_default_registry(self.router, self.memory, cfg)
        TaskManager(self.memory, PolicyExecutor(reg, lambda: cfg), lambda: cfg, lambda ev: None).register_tools(reg)
        return reg

    def test_every_built_in_tool_is_in_exactly_one_group(self):
        names = set(self.full_registry().names())
        listed = [t for g in groups.GROUPS.values() for t in g["tools"]]
        self.assertEqual(len(listed), len(set(listed)), "a tool in two groups")
        self.assertEqual(names - set(listed), set(), "new tools need a group in src/tools/groups.py")
        self.assertEqual(set(listed) - names, set(), "groups.py names a tool that doesn't exist")

    def test_a_new_install_only_has_the_safe_groups(self):
        names = set(build_default_registry(self.router, self.memory, self.cfg).names())
        for off in ("edit_file", "run_command", "open_terminal", "desktop_click", "desktop_screenshot", "send_email",
                    "draft_email", "delegate_to_coding_agent"):
            self.assertNotIn(off, names)
        for on in ("open_app", "read_webpage", "remember", "read_file", "search_files", "system_command"):
            self.assertIn(on, names)

    def test_switching_one_group_on(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["tools"]["groups"]["desktop"] = True
        names = set(build_default_registry(self.router, self.memory, cfg).names())
        self.assertTrue({"desktop_click", "desktop_snapshot"} <= names)
        self.assertNotIn("edit_file", names)

    def test_basics_cannot_be_switched_off_and_extension_tools_have_no_group(self):
        cfg = {"tools": {"groups": {"basic": False}}}
        self.assertTrue(groups.tool_enabled("remember", cfg))
        self.assertTrue(groups.tool_enabled("my_extension_tool", cfg))

    def test_presets_round_trip(self):
        for preset in groups.PRESETS:
            self.assertEqual(groups.preset_for({"tools": {"groups": groups.groups_setting(preset)}}), preset)
        self.assertEqual(groups.preset_for({}), "everyday", "the defaults are Everyday")

    def test_the_security_page_describes_every_group(self):
        page = (ROOT / "docs" / "SECURITY_MODEL.md").read_text(encoding="utf-8")
        for g in groups.GROUPS.values():
            self.assertIn(f"| {g['title']} |", page)


def taint_registry(calls):
    """Stand-in tools under real tool names, so their groups decide the taint level."""
    reg = ToolRegistry()

    def tool(name, risk="safe", category="action", untrusted=False, permission="allow"):
        def run(a, c):
            calls.append(name)
            return ToolResult(ok=True, content=f"{name} ran", untrusted=untrusted)
        reg.register(ToolSpec(name, name, {"type": "object", "properties": {}}, run, risk=risk, category=category,
                              permission=permission))

    tool("read_webpage", category="read", untrusted=True)            # outside content
    tool("read_file", category="read", untrusted=True)               # the user's project
    tool("edit_file", risk="sensitive")                              # a project tool
    tool("system_command", risk="sensitive")                         # outside the project
    tool("desktop_snapshot", risk="sensitive", category="read")      # risky, but only looks
    tool("get_datetime")
    return reg


class UntrustedContentTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.events = [], []
        self.cfg = {"tools": {"permissions": {}, "approval_timeout_ms": 1500, "default_timeout_s": 5}}
        self.broker = ApprovalBroker(emit=self.events.append)
        self.asked = []

        def prompter(req):
            self.asked.append(req)
            threading.Timer(0.02, lambda: self.broker.resolve(req.id, False, via="test")).start()
        self.broker.prompter = prompter
        self.ex = PolicyExecutor(taint_registry(self.calls), lambda: self.cfg, self.broker, None, self.events.append)

    def run_all(self, ctx, *names):
        return {n: self.ex.execute(n, {}, ctx).status for n in names}

    def test_nothing_asks_before_untrusted_content(self):
        self.assertEqual(set(self.run_all(ToolContext(), "edit_file", "system_command").values()), {"done"})
        self.assertEqual(self.asked, [])

    def test_after_a_web_page_every_change_asks(self):
        ctx = ToolContext()
        self.ex.execute("read_webpage", {}, ctx)
        self.assertEqual((ctx.tainted, ctx.taint_level), ("read_webpage", "outside"))
        self.assertEqual(self.run_all(ctx, "edit_file", "system_command", "desktop_snapshot", "get_datetime", "read_file"),
                         {"edit_file": "denied", "system_command": "denied", "desktop_snapshot": "done",
                          "get_datetime": "done", "read_file": "done"})
        self.assertEqual([r.tool for r in self.asked], ["edit_file", "system_command"])
        why = next(r for r in self.asked[0].rows if r["k"] == "Why")
        self.assertIn("read_webpage", why["sub"])
        self.assertEqual(self.asked[0].reason, "untrusted")
        self.assertTrue(self.asked[0].voice_ok, "a spoken yes is fine for these")

    def test_after_project_files_only_actions_outside_the_project_ask(self):
        ctx = ToolContext()
        self.ex.execute("read_file", {}, ctx)
        self.assertEqual(ctx.taint_level, "project")
        self.assertEqual(self.run_all(ctx, "edit_file", "system_command"),
                         {"edit_file": "done", "system_command": "denied"})
        self.ex.execute("read_webpage", {}, ctx)                         # outside content raises the level
        self.assertEqual(ctx.taint_level, "outside")
        self.assertEqual(self.ex.execute("edit_file", {}, ctx).status, "denied")
        self.ex.execute("read_file", {}, ctx)                            # and never lowers it again
        self.assertEqual(ctx.taint_level, "outside")

    def test_each_request_starts_clean(self):
        ctx = ToolContext()
        self.ex.execute("read_webpage", {}, ctx)
        self.assertEqual(self.ex.execute("edit_file", {}, ToolContext()).status, "done")

    def test_the_setting_turns_it_off_but_deny_still_wins(self):
        self.cfg["tools"]["untrusted_asks"] = False
        ctx = ToolContext()
        self.ex.execute("read_webpage", {}, ctx)
        self.assertEqual(self.ex.execute("edit_file", {}, ctx).status, "done")
        self.cfg["tools"]["permissions"]["edit_file"] = "deny"
        self.assertEqual(self.ex.execute("edit_file", {}, ctx).status, "denied")
        self.assertEqual(self.asked, [])


class ClickOnlyApprovalTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.events = [], []
        self.cfg = {"assistant": {"name": "Nova"}, "tools": {"permissions": {}, "approval_timeout_ms": 3000},
                    "voice_approval": {}}
        self.broker = ApprovalBroker(emit=self.events.append)
        reg = ToolRegistry()
        reg.register(ToolSpec("wipe", "Delete it", {"type": "object", "properties": {}},
                              lambda a, c: self.calls.append("wipe") or ToolResult(ok=True, content="gone"),
                              risk="destructive", permission="ask", always_ask=True, purpose=lambda a: "Delete notes.txt"))
        reg.register(ToolSpec("broken_rule", "Rule raises", {"type": "object", "properties": {}},
                              lambda a, c: ToolResult(ok=True, content="ran"), always_ask=lambda a: 1 / 0))
        self.ex = PolicyExecutor(reg, lambda: self.cfg, self.broker, None, self.events.append)

    def answer(self, *steps):
        """Each step: (allowed, spoken) tried in order on the pending request; records what resolve() returned."""
        self.results = []

        def prompter(req):
            self.req = req

            def go():
                for allowed, spoken in steps:
                    self.results.append(self.broker.resolve(req.id, allowed, via="test", spoken=spoken))
            threading.Timer(0.05, go).start()
        self.broker.prompter = prompter

    def test_always_ask_beats_an_allow_setting(self):
        self.cfg["tools"]["permissions"]["wipe"] = "allow"
        self.assertEqual(self.ex.permission_for("wipe", {}), "ask")
        self.assertEqual(self.ex.permission_for("broken_rule", {}), "ask", "a rule that fails asks")

    def test_a_spoken_yes_is_refused_but_a_click_works(self):
        self.answer((True, True), (True, False))
        r = self.ex.execute("wipe", {}, ToolContext(source="voice"))
        self.assertEqual(self.results, [False, True])
        self.assertTrue(r.ok)
        self.assertFalse(self.req.voice_ok)
        self.assertTrue(self.req.spoken.endswith("To allow it, click Allow or press Y."), self.req.spoken)
        self.assertTrue(self.req.spoken.startswith("Nova wants to delete notes.txt."))
        ev = next(e for e in self.events if e["type"] == "approval_request")
        self.assertFalse(ev["voice_ok"])

    def test_a_spoken_no_still_denies(self):
        self.answer((False, True))
        self.assertEqual(self.ex.execute("wipe", {}, ToolContext()).status, "denied")
        self.assertEqual(self.calls, [])

    def test_the_setting_allows_a_spoken_yes(self):
        self.cfg["voice_approval"]["high_risk"] = True
        self.answer((True, True))
        self.assertTrue(self.ex.execute("wipe", {}, ToolContext()).ok)
        self.assertTrue(self.req.voice_ok)
        self.assertTrue(self.req.spoken.endswith("Allow?"))

    def test_broker_refuses_spoken_yes_directly(self):
        req = ApprovalRequest(tool="x", args={}, title="t", kind_label="k", spoken="s", rows=[], timeout_s=1,
                              voice_ok=False)
        threading.Timer(0.05, lambda: self.broker.resolve(None, True, spoken=True)).start()
        self.assertEqual(self.broker.request(req), "timeout")


class RealToolRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = ActionRouter()
        cls.cfg = all_groups(config_store.load_config())
        cls.cfg["email"].update({"username": "me@example.com", "trusted_recipients": ["friend@example.com"]})
        cls.reg = build_default_registry(cls.router, None, cls.cfg)

    def test_delete_file_never_offers_always_allow(self):
        spec = self.reg.get("delete_file")
        self.assertIs(spec.always_ask, True)
        ex = PolicyExecutor(self.reg, lambda: dict(self.cfg, tools=dict(self.cfg["tools"], permissions={"delete_file": "allow"})))
        self.assertEqual(ex.permission_for("delete_file", {"path": "x.txt"}), "ask")

    def test_shutdown_and_restart_always_ask_whatever_the_profile_says(self):
        spec = self.reg.get("system_command")
        self.assertTrue(spec.always_asks({"command": "shutdown"}))
        self.assertTrue(spec.always_asks({"command": "restart"}))
        self.assertFalse(spec.always_asks({"command": "lock"}))
        self.assertFalse(spec.always_asks({"command": "cancel_shutdown"}), "shutdown /a only aborts one")
        profile = json.loads(json.dumps(self.router.profile))
        profile["system"]["shutdown"].pop("confirm", None)
        profile["system"]["power_off"] = {"command": "shutdown.exe /s /t 0"}
        with mock.patch.object(self.router, "profile", profile):
            self.assertTrue(spec.always_asks({"command": "shutdown"}))
            self.assertTrue(spec.always_asks({"command": "power_off"}), "a custom name for the same command")

    def test_email_to_strangers_always_asks(self):
        spec = self.reg.get("send_email")
        mail = {"subject": "Hi", "body": "Hello"}
        self.assertFalse(spec.always_asks(dict(mail, to=["friend@example.com"])))
        self.assertTrue(spec.always_asks(dict(mail, to=["friend@example.com", "someone@example.org"])))


class RedactionTests(unittest.TestCase):
    def test_provider_keys_and_http_credentials_are_masked(self):
        for kind, key in KEYS.items():
            self.assertEqual(redact(f"key {key} here"), f"key {MASK} here", kind)
        self.assertEqual(redact("Authorization: Bearer abc.def-ghi_jkl012345"), f"Authorization: Bearer {MASK}")
        self.assertEqual(redact('-H "Authorization: Basic dXNlcjpwYXNzd29yZA=="'), f'-H "Authorization: Basic {MASK}"')
        self.assertEqual(redact("token is Bearer eyJhbGciOiJIUzI1NiJ9xyz012345"), f"token is Bearer {MASK}")
        self.assertEqual(redact("https://alex:hunter2pass@example.com/repo.git"), f"https://alex:{MASK}@example.com/repo.git")

    def test_ordinary_text_is_left_alone(self):
        for text in ("task-management-system-overview-page", "desk-organizer-ideas-for-small-rooms",
                     "call hf_hub_download first", "Bearer of bad news", "http://localhost:8080/path",
                     "ask-the-expert-session-recording", "risk-assessment-template-2024"):
            self.assertEqual(redact(text), text)

    def test_keys_never_reach_events_logs_or_the_audit_trail(self):
        key = KEYS["openai project"]
        events = []
        memory = MemoryStore(temp_db())
        self.addCleanup(memory.close)
        reg = ToolRegistry()
        reg.register(ToolSpec("echo", "Echo", {"type": "object", "properties": {"text": {"type": "string"}}},
                              lambda a, c: ToolResult(ok=True, content=f"got {a['text']} and {KEYS['groq']}")))
        ex = PolicyExecutor(reg, lambda: {"tools": {}}, None, memory, events.append)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r = ex.execute("echo", {"text": f"use {key}"}, ToolContext())
        everything = json.dumps(events) + out.getvalue() + r.content + json.dumps(memory.recent_tool_runs(), default=str)
        for secret in (key, KEYS["groq"]):
            self.assertNotIn(secret, everything)
        self.assertIn(MASK, out.getvalue())

    def test_the_log_file_masks_keys_whoever_writes_them(self):
        from src.core.logging_setup import _RedactFilter
        record = logging.LogRecord("jarvis", logging.INFO, __file__, 1, "saving %s for %s", (KEYS["xai"], "xai"), None)
        self.assertTrue(_RedactFilter().filter(record))
        self.assertEqual(record.getMessage(), f"saving {MASK} for xai")


class DesktopBlockListTests(unittest.TestCase):
    def refusal(self, process, title):
        from src.tools import desktop_tools
        d = desktop_tools.Desktop({})
        with mock.patch.object(desktop_tools, "process_name", return_value=(4242, process)), \
                mock.patch.object(desktop_tools, "window_title", return_value=title):
            return d.refusal(1)

    def test_password_managers_and_password_pages_are_refused(self):
        self.assertIn("blocked_processes", self.refusal("bitwarden.exe", "Bitwarden"))
        self.assertIn("blocked_processes", self.refusal("keepassxc.exe", "Passwords.kdbx - KeePassXC"))
        self.assertIn("passwords", self.refusal("chrome.exe", "Google Password Manager - Google Chrome"))
        self.assertIn("passwords", self.refusal("firefox.exe", "about:logins — Mozilla Firefox"))
        self.assertIn("passwords", self.refusal("explorer.exe", "Credential Manager"))
        self.assertEqual(self.refusal("chrome.exe", "Inbox - Gmail - Google Chrome"), "")


class CredentialsAreNeverLoggedTests(unittest.TestCase):
    """Static check: what credentials.read returns (directly, or through api_key()[0] / get_password()) is never
    passed to print, a logger or an event emitter."""
    SINKS = {"print", "log", "debug", "info", "warning", "error", "exception", "critical", "emit", "_emit",
             "emit_event"}
    READERS = {"api_key", "get_password"}         # functions that return what credentials.read gave them

    @staticmethod
    def _name(func):
        return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""

    def _reads_secret(self, node) -> bool:
        """The expression is (or contains) a secret: credentials.read(...), get_password(...), api_key(...) or
        api_key(...)[0]; api_key(...)[1] is only where the key came from."""
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call) \
                and self._name(node.value.func) == "api_key":
            return isinstance(node.slice, ast.Constant) and node.slice.value == 0
        if isinstance(node, ast.Call):
            name = self._name(node.func)
            if name == "read" and isinstance(node.func, ast.Attribute) and self._name(node.func.value) == "credentials":
                return True
            if name in self.READERS:
                return True
        return any(self._reads_secret(child) for child in ast.iter_child_nodes(node))

    def test_no_module_logs_a_credential(self):
        files = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py")) + [ROOT / "jarvis_gui.pyw"]
        problems = []
        readers = set()
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                        isinstance(n, ast.Call) and self._name(n.func) == "read"
                        and isinstance(n.func, ast.Attribute) and self._name(n.func.value) == "credentials"
                        for n in ast.walk(fn)):
                    readers.add(fn.name)
                    if any(isinstance(n, ast.Call) and self._name(n.func) in self.SINKS for n in ast.walk(fn)):
                        problems.append(f"{path.name}:{fn.name} reads a credential and logs something")
            for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                secrets = set()
                for node in ast.walk(fn):
                    if isinstance(node, ast.Assign) and self._reads_secret(node.value):
                        for target in node.targets:
                            first = target.elts[0] if isinstance(target, ast.Tuple) and target.elts else target
                            if isinstance(first, ast.Name):
                                secrets.add(first.id)
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and self._name(node.func) in self.SINKS:
                        args = list(node.args) + [k.value for k in node.keywords]
                        if any(self._reads_secret(a) or any(isinstance(n, ast.Name) and n.id in secrets
                                                            for n in ast.walk(a)) for a in args):
                            problems.append(f"{path.name}:{node.lineno} passes a credential to {self._name(node.func)}()")
        self.assertEqual(readers, self.READERS, "a new function reads credentials: add it to READERS")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
