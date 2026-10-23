"""PolicyExecutor: validation, permissions, approvals, dry-run, timeouts, cancel, audit."""
import threading
import time
import unittest

from src.memory.store import MemoryStore
from src.tools.approvals import ApprovalBroker, classify_reply
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec
from tests.fakes import temp_db


def make_registry(calls):
    reg = ToolRegistry()

    def echo(args, ctx):
        calls.append(("echo", args))
        return ToolResult(ok=True, content=f"echo {args['text']}")

    def slow(args, ctx):
        calls.append(("slow", args))
        for _ in range(100):
            if ctx.cancel_event.is_set():
                return ToolResult(ok=False, content="stopped", status="failed")
            time.sleep(0.02)
        return ToolResult(ok=True, content="finished")

    def boom(args, ctx):
        raise RuntimeError("kaboom")

    reg.register(ToolSpec("echo", "Echo text", {"type": "object", "properties": {"text": {"type": "string", "maxLength": 10}},
                                                "required": ["text"]}, echo))
    reg.register(ToolSpec("sensitive", "Needs approval", {"type": "object", "properties": {}},
                          lambda a, c: calls.append(("sensitive", a)) or ToolResult(ok=True, content="did it"),
                          risk="sensitive", permission="ask"))
    reg.register(ToolSpec("maybe", "Asks only for 'big'", {"type": "object", "properties": {"size": {"type": "string"}}},
                          lambda a, c: calls.append(("maybe", a)) or ToolResult(ok=True, content="ok"),
                          needs_confirmation=lambda a: a.get("size") == "big"))
    reg.register(ToolSpec("slow", "Slow, cancellable", {"type": "object", "properties": {}}, slow, timeout_s=0.3, cancellable=True))
    reg.register(ToolSpec("boom", "Raises", {"type": "object", "properties": {}}, boom))
    return reg


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.cfg = {"tools": {"permissions": {}, "approval_timeout_ms": 1500, "default_timeout_s": 5}}
        self.memory = MemoryStore(temp_db())
        self.addCleanup(self.memory.close)
        self.broker = ApprovalBroker(emit=self.events.append)
        self.ex = PolicyExecutor(make_registry(self.calls), lambda: self.cfg, self.broker, self.memory, self.events.append)

    def auto_answer(self, allowed, delay=0.05):
        def prompter(req):
            threading.Timer(delay, lambda: self.broker.resolve(req.id, allowed, via="test")).start()
        self.broker.prompter = prompter

    def test_allowed_tool_runs_and_is_audited(self):
        r = self.ex.execute("echo", {"text": "hi"}, ToolContext(session_id="s1"))
        self.assertTrue(r.ok)
        self.assertEqual(r.content, "echo hi")
        self.assertEqual(self.calls, [("echo", {"text": "hi"})])
        run = self.memory.recent_tool_runs()[0]
        self.assertEqual((run["tool"], run["status"], run["decision"], run["session_id"]), ("echo", "done", "auto", "s1"))
        types = [e["type"] for e in self.events]
        self.assertEqual(types, ["tool_call", "tool_result"])

    def test_unknown_tool_is_invalid(self):
        r = self.ex.execute("rm_rf", {}, ToolContext())
        self.assertEqual(r.status, "invalid")
        self.assertIn("Unknown tool", r.content)

    def test_schema_violation_never_reaches_handler(self):
        for bad in ({}, {"text": 5}, {"text": "x" * 11}, {"text": "ok", "extra": 1}):
            r = self.ex.execute("echo", bad, ToolContext())
            self.assertEqual(r.status, "invalid", bad)
        self.assertEqual(self.calls, [])

    def test_deny_override_blocks_tool(self):
        self.cfg["tools"]["permissions"]["echo"] = "deny"
        r = self.ex.execute("echo", {"text": "hi"}, ToolContext())
        self.assertEqual(r.status, "denied")
        self.assertEqual(self.calls, [])

    def test_ask_then_allow(self):
        self.auto_answer(True)
        r = self.ex.execute("sensitive", {}, ToolContext())
        self.assertTrue(r.ok)
        self.assertEqual(self.calls, [("sensitive", {})])
        self.assertEqual(self.memory.recent_tool_runs()[0]["decision"], "approved")
        self.assertIn("approval_request", [e["type"] for e in self.events])

    def test_ask_then_deny(self):
        self.auto_answer(False)
        r = self.ex.execute("sensitive", {}, ToolContext())
        self.assertEqual(r.status, "denied")
        self.assertIn("nothing was executed", r.content)
        self.assertEqual(self.calls, [])

    def test_ask_times_out_to_deny(self):
        self.cfg["tools"]["approval_timeout_ms"] = 300
        t0 = time.time()
        r = self.ex.execute("sensitive", {}, ToolContext())
        self.assertEqual(r.status, "denied")
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(self.memory.recent_tool_runs()[0]["decision"], "timeout")
        self.assertEqual(self.calls, [])

    def test_no_broker_means_deny(self):
        ex = PolicyExecutor(make_registry(self.calls), lambda: self.cfg, None, None)
        self.assertEqual(ex.execute("sensitive", {}, ToolContext()).status, "denied")

    def test_allow_override_cannot_skip_per_argument_confirmation(self):
        self.cfg["tools"]["permissions"]["maybe"] = "allow"
        self.assertEqual(self.ex.permission_for("maybe", {"size": "small"}), "allow")
        self.assertEqual(self.ex.permission_for("maybe", {"size": "big"}), "ask")

    def test_settings_can_require_approval_for_safe_tool(self):
        self.cfg["tools"]["permissions"]["echo"] = "ask"
        self.auto_answer(False)
        self.assertEqual(self.ex.execute("echo", {"text": "x"}, ToolContext()).status, "denied")

    def test_timeout_stops_cancellable_tool(self):
        r = self.ex.execute("slow", {}, ToolContext())
        self.assertEqual(r.status, "timeout")

    def test_cancel_before_run(self):
        ctx = ToolContext()
        ctx.cancel_event.set()
        self.assertEqual(self.ex.execute("echo", {"text": "x"}, ctx).status, "cancelled")
        self.assertEqual(self.calls, [])

    def test_cancel_during_approval(self):
        ctx = ToolContext()
        threading.Timer(0.1, ctx.cancel_event.set).start()
        r = self.ex.execute("sensitive", {}, ctx)
        self.assertEqual(r.status, "cancelled")

    def test_cancel_while_running(self):
        ctx = ToolContext()
        threading.Timer(0.05, ctx.cancel_event.set).start()
        cfg_timeout = self.ex.registry.get("slow")
        cfg_timeout.timeout_s = 5
        r = self.ex.execute("slow", {}, ctx)
        self.assertEqual(r.status, "cancelled")

    def test_handler_exception_becomes_failed_result(self):
        r = self.ex.execute("boom", {}, ToolContext())
        self.assertEqual(r.status, "failed")
        self.assertIn("kaboom", r.content)
        self.assertEqual(self.memory.recent_tool_runs()[0]["status"], "failed")

    def test_concurrent_approval_requests_do_not_stack(self):
        hold = threading.Event()
        self.broker.prompter = lambda req: None
        results = []
        t = threading.Thread(target=lambda: results.append(self.ex.execute("sensitive", {}, ToolContext())))
        t.start()
        self.assertTrue(_wait(lambda: self.broker.pending is not None))
        second = self.ex.execute("sensitive", {}, ToolContext())
        self.assertEqual(second.status, "denied")
        self.broker.resolve(None, False)
        t.join(3)
        hold.set()

    def test_classify_reply(self):
        yes, no = ["yes", "go ahead", "do it"], ["no", "cancel", "don't"]
        self.assertEqual(classify_reply("Yes please", yes, no), "yes")
        self.assertEqual(classify_reply("go ahead.", yes, no), "yes")
        self.assertEqual(classify_reply("no", yes, no), "no")
        self.assertEqual(classify_reply("don't do it", yes, no), "no")
        self.assertEqual(classify_reply("nobody knows", yes, no), "unknown")
        self.assertEqual(classify_reply("", yes, no), "unknown")


def _wait(pred, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


if __name__ == "__main__":
    unittest.main()
