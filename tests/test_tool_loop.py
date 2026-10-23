"""The tool-calling loop (ToolLoopBackend) with a scripted model and the real PolicyExecutor.

What the model sees and what runs: tools go through the policy (validation, approvals, dry run); bad
arguments, unknown tools and denied calls come back as results the model can read; cancel, the time limit
and max turns stop it; images reach models that can see; the history only grows, is compacted with a
summary when it gets big, and reset() clears it; usage is recorded and the daily budget is enforced.
"""
import copy
import threading
import time
import unittest

from src.agent.base import AgentResult
from src.agent.providers.base import Capabilities, Completion, ProviderAdapter, ProviderError, ToolCall, Usage
from src.agent.tool_loop_backend import ToolLoopBackend
from src.agent.usage import UsageMeter
from src.memory.store import MemoryStore
from src.tools.approvals import ApprovalBroker
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec
from tests.fakes import temp_db

PROVIDER = {"kind": "openai_compat", "label": "Fake AI", "name": "fake", "timeout_seconds": 5,
            "history_budget_tokens": 60000}


def config(**over):
    cfg = {"llm": {"warmup": False, "prices": {"fake-model": {"input": 1.0, "output": 2.0}}},
           "agent": {"max_turns": 6, "max_seconds": 30, "memory_context_items": 0},
           "tools": {"permissions": {}, "approval_timeout_ms": 1000, "profile": "auto"}}
    for k, v in over.items():
        cfg[k] = dict(cfg.get(k, {}), **v)
    return cfg


def text(t, prompt=100, out=10):
    return Completion(text=t, tool_calls=[], stop_reason="end", usage=Usage(input_tokens=prompt, output_tokens=out))


def calls(*items, prompt=100):
    return Completion(text="", tool_calls=[ToolCall(id=f"c{i}", name=n, args=a) for i, (n, a) in enumerate(items)],
                      stop_reason="tool_use", usage=Usage(input_tokens=prompt, output_tokens=5))


class ScriptedAdapter(ProviderAdapter):
    """Returns the scripted completions in order and keeps a copy of every request."""
    kind = "scripted"

    def __init__(self, script, vision=False, settings=None):
        super().__init__("fake", "fake-model", dict(PROVIDER, **(settings or {})))
        self.script = list(script)
        self.seen = []
        self.capabilities = Capabilities(vision=vision)

    def _complete(self, system, messages, tools, timeout_s, holder):
        self.seen.append({"system": system, "messages": copy.deepcopy(messages), "tools": [t.name for t in tools]})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step()
        return step


def registry(calls_made):
    reg = ToolRegistry()
    reg.register(ToolSpec("open_app", "Open an app", {"type": "object", "properties": {
        "app_name": {"type": "string", "enum": ["notepad"]}}, "required": ["app_name"]},
        lambda a, c: calls_made.append(("open_app", a)) or ToolResult(ok=True, content="Opened: notepad (notepad.exe)")))
    reg.register(ToolSpec("danger", "Needs approval", {"type": "object", "properties": {}},
                          lambda a, c: calls_made.append(("danger", a)) or ToolResult(ok=True, content="did danger"),
                          permission="ask"))
    reg.register(ToolSpec("desktop_screenshot", "Screenshot", {"type": "object", "properties": {}},
                          lambda a, c: ToolResult(ok=True, content="Screenshot of the window",
                                                  data={"images": [{"data": "QUJD", "mimeType": "image/png"}]})))
    reg.register(ToolSpec("get_datetime", "Clock", {"type": "object", "properties": {}},
                          lambda a, c: ToolResult(ok=True, content="Friday 10:00")))
    reg.register(ToolSpec("simulate", "A tool in dry-run mode", {"type": "object", "properties": {}},
                          lambda a, c: ToolResult(ok=True, content="Would open notepad", status="dry_run", dry_run=True)))
    return reg


class ToolLoopTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.memory = MemoryStore(temp_db())
        self.addCleanup(self.memory.close)
        self.broker = ApprovalBroker()
        self.broker.prompter = lambda req: self.broker.resolve(req.id, False, via="test")

    def backend(self, script, cfg=None, vision=False, settings=None, summarizer=None, meter=True):
        cfg = cfg or config()
        executor = PolicyExecutor(registry(self.calls), lambda: cfg, self.broker, self.memory)
        self.adapter = ScriptedAdapter(script, vision=vision, settings=settings)
        b = ToolLoopBackend(cfg, executor, self.adapter, summarizer=summarizer,
                            meter=UsageMeter(self.memory, lambda: cfg) if meter else None)
        b.load()
        self.addCleanup(b.unload)
        return b

    def run_turn(self, b, turn="hello", ctx=None):
        self.events = []
        return b.run(turn, ctx or ToolContext(session_id="t"), self.events.append)

    def results_seen(self, request_index=-1):
        """The tool results the model saw in a request."""
        return [r for m in self.adapter.seen[request_index]["messages"] if m.role == "tool_results" for r in m.results]

    # ------------------------------------------------------------ answers and tools

    def test_plain_answer(self):
        b = self.backend([text("Hi there")])
        res = self.run_turn(b)
        self.assertEqual((res.ok, res.reply, res.turns, res.tool_calls), (True, "Hi there", 1, []))
        self.assertEqual(self.adapter.seen[0]["messages"][0].text, "hello")
        self.assertIn("You are Jarvis", self.adapter.seen[0]["system"])

    def test_tool_runs_through_the_policy_and_the_model_reads_the_result(self):
        b = self.backend([calls(("open_app", {"app_name": "notepad"})), text("Notepad is open.")])
        res = self.run_turn(b, "open notepad")
        self.assertEqual(res.reply, "Notepad is open.")
        self.assertEqual(res.tool_calls, [{"tool": "open_app", "args": {"app_name": "notepad"}}])
        self.assertEqual(self.calls, [("open_app", {"app_name": "notepad"})])
        self.assertEqual(self.results_seen()[0].text, "Opened: notepad (notepad.exe)")
        self.assertEqual(len(self.memory.recent_tool_runs()), 1, "the policy logged the run")

    def test_a_simulated_result_reaches_the_model_marked_dry_run(self):
        b = self.backend([calls(("simulate", {})), text("That was only simulated.")])
        self.run_turn(b)
        self.assertTrue(self.results_seen()[0].text.startswith("DRY RUN"))

    def test_denied_invalid_unknown_and_malformed_calls_come_back_as_results(self):
        bad_json = ToolCall(id="m", name="open_app", args={}, error="the arguments were not valid JSON (x)")
        comp = calls(("danger", {}), ("open_app", {"app_name": "rm"}), ("format_disk", {}))
        comp.tool_calls.append(bad_json)
        b = self.backend([comp, text("Some of that didn't work.")])
        res = self.run_turn(b)
        texts = [r.text for r in self.results_seen()]
        self.assertTrue(texts[0].startswith("DENIED"))
        self.assertTrue(texts[1].startswith("INVALID ARGUMENTS"))
        self.assertIn("no tool called 'format_disk'", texts[2])
        self.assertIn("not valid JSON", texts[3])
        self.assertTrue(all(r.is_error for r in self.results_seen()))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(res.tool_calls), 4)

    def test_a_tool_outside_the_profile_is_not_offered_or_run(self):
        cfg = config(tools={"profile": "custom", "custom_list": ["get_datetime"]})
        b = self.backend([calls(("open_app", {"app_name": "notepad"})), text("I can't.")], cfg=cfg)
        self.run_turn(b)
        self.assertEqual(self.adapter.seen[0]["tools"], ["get_datetime"])
        self.assertEqual(self.calls, [])
        self.assertNotIn("open_app", self.adapter.seen[0]["system"].split("tools (")[1].split(")")[0])

    def test_local_providers_get_the_short_tool_list(self):
        b = self.backend([text("ok")], settings={"local": True})
        self.run_turn(b)
        self.assertEqual(self.adapter.seen[0]["tools"], ["open_app", "get_datetime"])     # the core list

    # ------------------------------------------------------------ images

    def test_images_reach_a_model_that_can_see(self):
        b = self.backend([calls(("desktop_screenshot", {})), text("I see it.")], vision=True)
        self.run_turn(b)
        self.assertEqual(self.results_seen()[0].images, [{"data": "QUJD", "mimeType": "image/png"}])

    def test_images_become_a_note_for_a_text_only_model(self):
        b = self.backend([calls(("desktop_screenshot", {})), text("Text only.")], vision=False)
        self.run_turn(b)
        r = self.results_seen()[0]
        self.assertEqual(r.images, [])
        self.assertIn("can't see images", r.text)

    def test_a_server_that_refuses_images_is_retried_without_them(self):
        b = self.backend([calls(("desktop_screenshot", {})), ProviderError("no_vision", "no images"), text("Fine.")],
                         vision=True)
        res = self.run_turn(b)
        self.assertEqual(res.reply, "Fine.")
        self.assertFalse(self.adapter.capabilities.vision)
        self.assertEqual(self.results_seen()[0].images, [])

    # ------------------------------------------------------------ stopping

    def test_cancel_before_the_model_is_asked(self):
        b = self.backend([text("never")])
        ctx = ToolContext()
        ctx.cancel_event.set()
        res = self.run_turn(b, ctx=ctx)
        self.assertTrue(res.cancelled)
        self.assertEqual(self.adapter.seen, [])

    def test_cancel_while_waiting_for_the_model(self):
        ctx = ToolContext()

        def slow():
            time.sleep(3)
            return text("late")
        b = self.backend([slow])
        threading.Timer(0.2, ctx.cancel_event.set).start()
        t0 = time.time()
        res = self.run_turn(b, ctx=ctx)
        self.assertTrue(res.cancelled)
        self.assertLess(time.time() - t0, 2)

    def test_cancel_between_tool_calls_answers_every_call(self):
        ctx = ToolContext()
        cfg = config()
        reg_calls = self.calls

        b = self.backend([calls(("open_app", {"app_name": "notepad"}), ("get_datetime", {}))], cfg=cfg)
        original = b.executor.execute

        def execute(name, args, c):
            result = original(name, args, c)
            ctx.cancel_event.set()                  # the user pressed stop during the first call
            return result
        b.executor.execute = execute
        res = self.run_turn(b, ctx=ctx)
        self.assertTrue(res.cancelled)
        last = b.history[-1]
        self.assertEqual([r.call_id for r in last.results], ["c0", "c1"])
        self.assertIn("CANCELLED", last.results[1].text)
        self.assertEqual(reg_calls, [("open_app", {"app_name": "notepad"})])

    def test_task_pause_interrupt_stops_the_turn(self):
        ctx = ToolContext(interrupt_event=threading.Event())
        ctx.interrupt_event.set()
        res = self.run_turn(self.backend([text("never")]), ctx=ctx)
        self.assertTrue(res.cancelled)

    def test_max_turns(self):
        cfg = config(agent={"max_turns": 2})
        b = self.backend([calls(("get_datetime", {})), calls(("get_datetime", {})), text("unused")], cfg=cfg)
        res = self.run_turn(b)
        self.assertTrue(res.ok)
        self.assertEqual(res.turns, 2)
        self.assertIn("more steps", res.reply)

    def test_total_time_limit(self):
        cfg = config(agent={"max_seconds": 1})

        def slow():
            time.sleep(0.7)
            return calls(("get_datetime", {}))
        b = self.backend([slow, slow, slow], cfg=cfg)
        res = self.run_turn(b)
        self.assertFalse(res.ok)
        self.assertIn("time limit", res.error)
        self.assertTrue(res.spoken)

    def test_provider_error_is_reported_with_a_spoken_line(self):
        b = self.backend([ProviderError("auth", "API key rejected")])
        res = self.run_turn(b)
        self.assertFalse(res.ok)
        self.assertIn("API key rejected", res.error)
        self.assertIn("API key", res.spoken)

    def test_refusal_and_cut_off_answers(self):
        refusal = Completion(text="", tool_calls=[], stop_reason="refusal", usage=Usage())
        cut = Completion(text="", tool_calls=[], stop_reason="max_tokens", usage=Usage())
        b = self.backend([refusal, cut])
        self.assertEqual(self.run_turn(b).reply, "I can't help with that one.")
        self.assertIn("cut off", self.run_turn(b).reply)

    # ------------------------------------------------------------ history

    def test_history_only_grows_across_turns_and_reset_clears_it(self):
        b = self.backend([calls(("get_datetime", {})), text("Ten."), text("You're welcome.")])
        self.run_turn(b, "time?")
        self.run_turn(b, "thanks")
        first, second = self.adapter.seen[1]["messages"], self.adapter.seen[2]["messages"]
        self.assertEqual([m.role for m in second], ["user", "assistant", "tool_results", "assistant", "user"])
        self.assertEqual(second[:len(first)], first, "earlier messages are sent unchanged")
        b.reset()
        self.assertEqual(b.history, [])

    def test_a_big_conversation_is_summarized_before_the_next_turn(self):
        prompts = []
        b = self.backend([text("First answer.", prompt=70000), text("Second answer.")],
                         summarizer=lambda p: prompts.append(p) or "The user asked about the weather.")
        self.run_turn(b, "what's the weather")
        self.run_turn(b, "and tomorrow?")
        self.assertIn("USER: what's the weather", prompts[0])
        self.assertIn("JARVIS: First answer.", prompts[0])
        sent = self.adapter.seen[1]["messages"]
        self.assertEqual([m.role for m in sent], ["user", "user"])
        self.assertIn("The user asked about the weather.", sent[0].text)
        self.assertEqual(sent[1].text, "and tomorrow?")

    def test_when_the_summary_fails_the_conversation_is_cleared(self):
        def broken(prompt):
            raise RuntimeError("utility model down")
        b = self.backend([text("a", prompt=70000), text("b")], summarizer=broken)
        self.run_turn(b)
        self.run_turn(b)
        self.assertIn("cleared to save space", self.adapter.seen[1]["messages"][0].text)

    def test_orchestrator_style_reprompts_continue_the_same_conversation(self):
        b = self.backend([text("Started."), text("Finished.")])
        self.run_turn(b, "do the task")
        res = b.run("[orchestrator] Task #1 is still RUNNING ...", ToolContext(task_id=1), lambda e: None)
        self.assertEqual(res.reply, "Finished.")
        self.assertEqual([m.role for m in self.adapter.seen[1]["messages"]], ["user", "assistant", "user"])

    # ------------------------------------------------------------ usage and budget

    def test_usage_is_recorded_and_emitted(self):
        b = self.backend([text("ok", prompt=1000, out=500)])
        self.run_turn(b)
        ev = [e for e in self.events if e.get("type") == "usage"][0]
        self.assertEqual((ev["provider"], ev["model"], ev["input_tokens"], ev["output_tokens"]), ("fake", "fake-model", 1000, 500))
        self.assertAlmostEqual(ev["cost_usd"], (1000 * 1.0 + 500 * 2.0) / 1e6)
        day = self.memory.usage_by_day(1)[0]
        self.assertEqual((day["requests"], day["input_tokens"], day["output_tokens"]), (1, 1000, 500))

    def test_the_daily_budget_stops_new_requests(self):
        cfg = config(llm={"daily_budget_usd": 0.001})
        b = self.backend([text("expensive", prompt=1000, out=1000), text("never")], cfg=cfg)
        self.assertTrue(self.run_turn(b).ok)
        res = self.run_turn(b)
        self.assertFalse(res.ok)
        self.assertEqual(res.stage, "budget")
        self.assertIn("spending limit", res.spoken)
        self.assertEqual(len(self.adapter.seen), 1, "the model was not asked again")

    def test_warmup_checks_the_connection(self):
        cfg = config(llm={"warmup": True})
        executor = PolicyExecutor(registry(self.calls), lambda: cfg, self.broker, self.memory)
        bad = ToolLoopBackend(cfg, executor, ScriptedAdapter([ProviderError("network", "refused")]))
        with self.assertRaises(RuntimeError) as cm:
            bad.load()
        self.assertIn("refused", str(cm.exception))
        good = ToolLoopBackend(cfg, executor, ScriptedAdapter([text("ready")]))
        good.load()
        self.assertEqual(good.history, [], "the start-up check is not part of the conversation")

    def test_run_before_load_raises(self):
        cfg = config()
        executor = PolicyExecutor(registry(self.calls), lambda: cfg, self.broker, self.memory)
        with self.assertRaises(RuntimeError):
            ToolLoopBackend(cfg, executor, ScriptedAdapter([])).run("x", ToolContext(), lambda e: None)

    def test_a_tool_that_raises_still_answers_the_call(self):
        b = self.backend([calls(("get_datetime", {})), text("The clock failed."), text("Next answer.")])
        b.executor.execute = lambda name, args, ctx: (_ for _ in ()).throw(OSError("disk gone"))
        self.assertEqual(self.run_turn(b).reply, "The clock failed.")
        self.assertIn("disk gone", self.results_seen()[0].text)
        self.assertEqual(self.run_turn(b).reply, "Next answer.")

    def test_a_bug_in_the_loop_still_returns_a_result(self):
        b = self.backend([lambda: (_ for _ in ()).throw(ValueError("boom"))])
        res = self.run_turn(b)
        self.assertIsInstance(res, AgentResult)
        self.assertFalse(res.ok)
        self.assertIn("boom", res.error)


if __name__ == "__main__":
    unittest.main()
