"""AssistantController: lifecycle, typed approvals, queueing, cancel, clean stop."""
import threading
import time
import unittest

import src.pipeline as pipeline
from src.agent import Agent
from src.agent.base import AgentResult
from src.app.controller import AssistantController
from src.core.config_store import load_config as load_full
from src.memory.store import MemoryStore
from src.router.action_router import ActionRouter
from src.tools.policy import PolicyExecutor
from src.tools.registry import build_default_registry
from tests.fakes import (FakeAudio, FakeGreeter, FakeStream, FakeTTS, FakeTranscriber, ScriptedBackend, fake_vad,
                         temp_db, test_config, wait_for)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = test_config(load_full())
        self._orig = pipeline.load_config
        pipeline.load_config = lambda: self.cfg
        self.memory = MemoryStore(temp_db())
        self.events = []
        self.gate = threading.Event()
        self.gate.set()

    def tearDown(self):
        pipeline.load_config = self._orig
        self.memory.close()

    def make(self, behaviour):
        stream = FakeStream()
        router = ActionRouter()
        router.dry_run = True
        self.controller = AssistantController(memory=self.memory)
        executor = PolicyExecutor(build_default_registry(router, self.memory, self.cfg), lambda: self.cfg,
                                  self.controller.broker, self.memory)
        backend = ScriptedBackend(executor, behaviour)
        self.backend = backend
        self.controller._pipeline_kwargs = {"components": {
            "audio": FakeAudio(stream), "transcriber": FakeTranscriber([]), "vad": fake_vad, "greeter": FakeGreeter(),
            "tts": FakeTTS(stream), "agent": Agent(backend, self.memory, self.cfg), "router": router, "executor": executor,
            "stop_playback": lambda: None, "skip_validation": True}}
        self.controller.add_listener(self.events.append)
        return self.controller

    def has(self, type_, **m):
        return any(e.get("type") == type_ and all(e.get(k) == v for k, v in m.items()) for e in list(self.events))

    def test_start_send_stop_releases_thread(self):
        c = self.make(lambda t, ctx, ex: AgentResult(reply="Done."))
        self.assertEqual(c.send_text("hello"), "rejected")          # not running yet
        self.assertTrue(self.has("error", stage="ui"))
        self.assertTrue(c.start())
        self.assertFalse(c.start())                                  # no second pipeline
        self.assertTrue(wait_for(lambda: c.ready_info.get("type") == "ready"))
        self.assertTrue(c.voice_ok)
        self.assertEqual(c.send_text("hello"), "sent")
        self.assertTrue(wait_for(lambda: self.has("reply", text="Done.")))
        self.assertTrue(c.stop(wait=10))
        self.assertFalse(c.running)
        self.assertTrue(self.has("stopped"))
        self.assertEqual(c.phase, "stopped")
        self.assertTrue(self.backend.unloaded)

    def test_send_remote_routes_reply_and_answers_approvals(self):
        mem = {"id": self.memory.add_memory("temporary fact")["id"]}

        def behaviour(t, ctx, ex):
            if "forget" in t:
                res = ex.execute("forget", {"memory_id": mem["id"]}, ctx)
                return AgentResult(reply="Deleted." if res.ok else res.content)
            return AgentResult(reply="Done.")
        c = self.make(behaviour)
        self.assertEqual(c.send_remote(text="hello", ref="ph:1:1"), "stopped")
        self.assertEqual(c.send_remote(), "rejected")
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        self.assertEqual(c.send_remote(text="hello", ref="ph:1:2"), "sent")
        self.assertTrue(wait_for(lambda: self.has("reply", text="Done.", ref="ph:1:2", source="phone")))
        c.set_muted(True)                                      # muting the mic doesn't block phone commands
        self.assertEqual(c.send_remote(text="forget that", ref="ph:1:3"), "sent")
        self.assertTrue(wait_for(lambda: c.broker.pending is not None))
        self.assertEqual(c.status()["pending_approval"], c.broker.pending.title)
        self.assertEqual(c.send_remote(text="yes", ref="ph:1:4"), "approval")
        self.assertTrue(wait_for(lambda: self.has("reply", text="Deleted.", ref="ph:1:3")))
        self.assertTrue(self.has("approval_resolved", decision="allow", via="phone 'yes'"))
        c.stop()

    def test_commands_queue_while_busy(self):
        def slow(t, ctx, ex):
            time.sleep(0.4)
            return AgentResult(reply="ok")
        c = self.make(slow)
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        c.send_text("first")
        self.assertTrue(wait_for(lambda: c.busy))
        self.assertEqual(c.send_text("second"), "queued")
        self.assertTrue(wait_for(lambda: len([e for e in self.events if e["type"] == "timing"]) == 2, 10))
        c.stop()

    def test_typed_yes_resolves_pending_approval(self):
        mid = self.memory.add_memory("delete me")["id"]

        def forget(t, ctx, ex):
            r = ex.execute("forget", {"memory_id": mid}, ctx)
            return AgentResult(reply=r.content)
        c = self.make(forget)
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        c.send_text("forget it")
        self.assertTrue(wait_for(lambda: c.broker.pending is not None))
        self.assertEqual(c.send_text("Yes"), "approval")
        self.assertTrue(wait_for(lambda: self.has("approval_resolved", decision="allow")))
        self.assertTrue(wait_for(lambda: self.memory.get_memory(mid) is None))
        c.stop()

    def test_cancel_denies_pending_approval_then_aborts_running_command(self):
        mid = self.memory.add_memory("keep me")["id"]

        def forget(t, ctx, ex):
            r = ex.execute("forget", {"memory_id": mid}, ctx)
            return AgentResult(reply=r.content)
        c = self.make(forget)
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        c.send_text("forget it")
        self.assertTrue(wait_for(lambda: c.broker.pending is not None))
        self.assertTrue(c.cancel())
        self.assertTrue(wait_for(lambda: self.has("approval_resolved", decision="deny")))
        self.assertIsNotNone(self.memory.get_memory(mid))
        self.assertTrue(wait_for(lambda: c.phase == "idle"))
        self.assertFalse(c.cancel())                                 # nothing to cancel when idle
        c.stop()

    def test_mute_and_listen_guards(self):
        c = self.make(lambda t, ctx, ex: AgentResult(reply="ok"))
        self.assertFalse(c.listen())                                 # not running
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        c.set_muted(True)
        self.assertTrue(c.muted)
        self.assertTrue(self.has("mute", muted=True))
        c.set_muted(False)
        self.assertTrue(c.ptt_press())
        c.ptt_release()
        c.stop()

    def test_greet_when_window_opens_but_not_when_busy_or_twice_in_a_row(self):
        c = self.make(lambda t, ctx, ex: AgentResult(reply="ok"))
        self.assertFalse(c.greet())                                  # not running
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        self.assertTrue(wait_for(lambda: c.phase == "idle"))
        self.assertTrue(c.greet())
        self.assertTrue(wait_for(lambda: self.has("reply", greeting=True)))
        self.assertFalse(c.greet())                                  # one just played
        c._greeted_at = 0
        c.phase = "thinking"
        self.assertFalse(c.greet())                                  # busy: no greeting over a command
        c.stop()

    def test_pause_and_cancel_inactive_tasks(self):
        c = self.make(lambda t, ctx, ex: AgentResult(reply="ok"))
        t1 = self.memory.create_task("one")
        self.memory.update_task(t1["id"], status="BLOCKED")
        self.assertEqual(c.pause_task(t1["id"]), "paused")
        self.assertEqual(self.memory.get_task(t1["id"])["status"], "PAUSED")
        self.assertEqual(c.cancel_task(t1["id"]), "cancelled")
        self.assertEqual(self.memory.get_task(t1["id"])["status"], "CANCELLED")
        self.assertEqual(c.cancel_task(t1["id"]), "not-active")
        self.assertEqual(c.resume_task(t1["id"]), "rejected")       # assistant not running
        self.assertTrue(self.has("task_update"))

    def test_stop_while_command_running_is_clean(self):
        started = threading.Event()

        def blocking(t, ctx, ex):
            started.set()
            ctx.cancel_event.wait(10)
            return AgentResult(reply="", ok=False, cancelled=True)
        c = self.make(blocking)
        c.start()
        self.assertTrue(wait_for(lambda: c.ready_info))
        c.send_text("long job")
        self.assertTrue(started.wait(5))
        t0 = time.time()
        self.assertTrue(c.stop(wait=10))
        self.assertLess(time.time() - t0, 5)


if __name__ == "__main__":
    unittest.main()
