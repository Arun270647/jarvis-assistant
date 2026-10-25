"""The real src.pipeline.main() driven with scripted audio and fakes for the hardware/models.

Covers the interaction contract: orb click -> capture -> STT -> agent -> tool
registry/policy -> spoken reply -> idle, plus push-to-talk, typed commands, window
greetings, approvals, cancel, error recovery and draining Jarvis's own voice.
"""
import queue
import threading
import time
import unittest

import src.pipeline as pipeline
from src.agent import Agent
from src.agent.base import AgentResult
from src.core.config_store import load_config as load_full
from src.memory.store import MemoryStore
from src.router.action_router import ActionRouter
from src.tools.approvals import ApprovalBroker
from src.tools.policy import PolicyExecutor
from src.tools.registry import build_default_registry
from tests.fakes import (SILENCE, SPEECH, FakeAudio, FakeGreeter, FakeStream, FakeTTS, FakeTranscriber,
                         ScriptedBackend, fake_vad, temp_db, test_config, wait_for)


def open_calculator(turn, ctx, executor):
    res = executor.execute("open_app", {"app_name": "calculator"}, ctx)    # an app shipped on both systems
    return AgentResult(reply=("Simulated opening Calculator." if res.dry_run else "Opened Calculator.") if res.ok else res.content,
                       tool_calls=[{"tool": "open_app", "status": res.status}])


class PipelineHarness:
    def __init__(self, behaviour, texts=(), script=(), tts_delay=0.0):
        self.cfg = test_config(load_full())
        self.stream = FakeStream(script)
        self.stt = FakeTranscriber(list(texts))
        self.tts = FakeTTS(self.stream, delay=tts_delay)
        self.memory = MemoryStore(temp_db())
        self.events = []
        self.states = []
        self.commands = queue.Queue()
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.ptt = threading.Event()
        self.listen = threading.Event()
        self.broker = ApprovalBroker()
        router = ActionRouter()
        router.dry_run = True
        self.executor = PolicyExecutor(build_default_registry(router, self.memory, self.cfg), lambda: self.cfg,
                                       self.broker, self.memory)
        self.backend = ScriptedBackend(self.executor, behaviour)
        self.agent = Agent(self.backend, self.memory, self.cfg)
        self.components = {"audio": FakeAudio(self.stream), "transcriber": self.stt, "vad": fake_vad,
                           "tts": self.tts, "agent": self.agent, "router": router, "executor": self.executor,
                           "stop_playback": lambda: None, "skip_validation": True}
        self.exit_code = None

    def start(self):
        self._orig = pipeline.load_config
        pipeline.load_config = lambda: self.cfg
        pipeline.stop_flag = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert wait_for(lambda: self.has("ready")), "pipeline never became ready"
        return self

    def _run(self):
        self.exit_code = pipeline.main(
            state_callback=self.states.append, stop_event=self.stop, event_callback=self.events.append,
            command_queue=self.commands, pause_event=self.pause, cancel_event=self.cancel, ptt_event=self.ptt,
            approval_broker=self.broker, memory=self.memory, session_id="test", components=self.components,
            listen_event=self.listen)

    def finish(self):
        self.stop.set()
        self.thread.join(10)
        pipeline.load_config = self._orig
        self.memory.close()
        assert not self.thread.is_alive(), "pipeline did not stop"

    def has(self, type_, **match):
        return any(e.get("type") == type_ and all(e.get(k) == v for k, v in match.items()) for e in list(self.events))

    def of(self, type_):
        return [e for e in list(self.events) if e.get("type") == type_]

    def phases(self):
        return [e["phase"] for e in self.of("phase")]

    def wait_idle_after(self, type_, timeout=10):
        def done():
            evs = list(self.events)
            idx = [i for i, e in enumerate(evs) if e.get("type") == type_]
            return idx and any(e.get("type") == "phase" and e.get("phase") == "idle" for e in evs[idx[-1]:])
        return wait_for(done, timeout)


class VoiceFlowTests(unittest.TestCase):
    def click_and_say(self, h, speech_chunks=15):
        h.stream.add_script([SILENCE] * 2)
        h.listen.set()
        h.stream.add_script([SPEECH] * speech_chunks + [SILENCE] * 20)

    def test_full_voice_flow_orb_click_command_tool_reply_idle(self):
        h = PipelineHarness(open_calculator, texts=["open calculator"]).start()
        try:
            self.click_and_say(h)
            self.assertTrue(h.wait_idle_after("timing"), h.events)
            self.assertFalse(h.listen.is_set())
            # No acknowledgement: the click goes straight to listening
            self.assertEqual(h.tts.spoken, ["Simulated opening Calculator."])
            self.assertTrue(h.has("phase", phase="listening", mode="tap"))
            # Whisper output reached the agent, the agent's tool call reached the registry/policy
            self.assertTrue(h.has("transcript", text="open calculator", source="voice"))
            self.assertIn("open calculator", h.backend.turns[0])
            call = h.of("tool_call")[0]
            self.assertEqual((call["tool"], call["args"]), ("open_app", {"app_name": "calculator"}))
            result = h.of("tool_result")[0]
            self.assertEqual((result["status"], result["dry_run"]), ("dry_run", True))
            self.assertTrue(h.has("reply", text="Simulated opening Calculator."))
            ph = h.phases()
            for a, b in [("listening", "transcribing"), ("transcribing", "thinking"), ("thinking", "executing"),
                         ("executing", "speaking")]:
                self.assertLess(ph.index(a), ph.index(b), (a, b, ph))
            self.assertNotIn("wake", ph)
            self.assertEqual(ph[-1], "idle")
            self.assertIn("listening", h.states)
            roles = [m["role"] for m in h.memory.recent_messages()]
            self.assertEqual(roles, ["user", "assistant"])
        finally:
            h.finish()
        self.assertEqual(h.exit_code, 0)
        self.assertTrue(h.backend.unloaded)
        self.assertTrue(h.has("phase", phase="stopped"))

    def test_ready_event_has_no_wake_word(self):
        h = PipelineHarness(open_calculator).start()
        try:
            ready = h.of("ready")[0]
            self.assertNotIn("wake_word", ready)
            self.assertNotIn("wake_phrase", ready)
        finally:
            h.finish()

    def test_speech_without_a_click_is_ignored(self):
        h = PipelineHarness(open_calculator, texts=["open calculator"], script=[SPEECH] * 30 + [SILENCE] * 30).start()
        try:
            self.assertTrue(wait_for(lambda: not h.stream.script))
            time.sleep(0.2)
            self.assertEqual(h.stt.calls, 0)
            self.assertEqual(h.backend.turns, [])
            self.assertNotIn("listening", h.phases())
        finally:
            h.finish()

    def test_addressing_jarvis_is_stripped(self):
        h = PipelineHarness(open_calculator, texts=["Jarvis, open calculator."]).start()
        try:
            self.click_and_say(h)
            self.assertTrue(h.wait_idle_after("timing"), h.events)
            self.assertTrue(h.has("transcript", text="open calculator.", source="voice"))
        finally:
            h.finish()

    def test_no_speech_after_click_returns_to_idle(self):
        h = PipelineHarness(open_calculator).start()
        try:
            h.listen.set()
            h.stream.add_script([SILENCE] * 60)
            self.assertTrue(wait_for(lambda: h.has("info", detail="No speech heard")))
            self.assertTrue(wait_for(lambda: h.phases()[-1] == "idle"))
            self.assertEqual(h.backend.turns, [])
        finally:
            h.finish()

    def test_stt_failure_is_reported_and_recovers(self):
        class BrokenSTT:
            def transcribe_audio(self, audio):
                raise RuntimeError("CUDA out of memory")
        h = PipelineHarness(open_calculator)
        h.components["transcriber"] = BrokenSTT()
        h.start()
        try:
            self.click_and_say(h, 20)
            self.assertTrue(wait_for(lambda: h.has("error", stage="stt")))
            self.assertTrue(wait_for(lambda: h.phases()[-1] == "idle"))
            h.commands.put("open calculator")   # still usable afterwards
            self.assertTrue(h.wait_idle_after("timing"))
        finally:
            h.finish()

    def test_muted_mic_ignores_clicks_and_push_to_talk(self):
        h = PipelineHarness(open_calculator, texts=["open calculator"])
        h.pause.set()
        h.start()
        try:
            self.click_and_say(h)
            self.assertTrue(wait_for(lambda: h.has("info", detail="Microphone is muted")))
            h.ptt.set()
            h.stream.add_script([SPEECH] * 10)
            time.sleep(0.3)
            h.ptt.clear()
            self.assertEqual(h.stt.calls, 0)
            h.pause.clear()                              # unmuted: push-to-talk works
            h.ptt.set()
            h.stream.add_script([SPEECH] * 10)
            self.assertTrue(wait_for(lambda: h.has("phase", phase="listening", mode="ptt")))
            time.sleep(0.1)
            h.ptt.clear()
            self.assertTrue(h.wait_idle_after("timing"))
            self.assertEqual(h.stt.calls, 1)
        finally:
            h.finish()

    def test_own_voice_is_drained_before_listening(self):
        # The reply's echo (20 speech-like chunks) is waiting in the mic buffer when the user clicks
        # again and says nothing: it must be dropped, not transcribed as a command.
        h = PipelineHarness(lambda t, c, e: AgentResult(reply="Hello there."), texts=["hi"]).start()
        try:
            self.click_and_say(h)
            self.assertTrue(h.wait_idle_after("timing"))
            h.stream.echo([SPEECH] * 20)
            h.listen.set()
            h.stream.add_script([SILENCE] * 60)
            self.assertTrue(wait_for(lambda: h.has("info", detail="No speech heard")))
            self.assertEqual(h.stt.calls, 1)
            self.assertEqual(len(h.backend.turns), 1)
        finally:
            h.finish()


class GreetingTests(unittest.TestCase):
    def test_greet_command_speaks_a_new_greeting_each_time(self):
        h = PipelineHarness(open_calculator)
        h.components["greeter"] = FakeGreeter()
        h.start()
        try:
            h.commands.put({"type": "greet"})
            self.assertTrue(wait_for(lambda: h.has("reply", text="Hey, greeting number 1.", greeting=True)))
            h.commands.put({"type": "greet"})
            self.assertTrue(wait_for(lambda: h.has("reply", text="Hey, greeting number 2.", greeting=True)))
            self.assertTrue(wait_for(lambda: h.phases()[-1] == "idle"))
            self.assertEqual(h.tts.spoken, ["Hey, greeting number 1.", "Hey, greeting number 2."])
            self.assertEqual(h.memory.recent_messages(), [])       # greetings are not conversation
            self.assertEqual(h.backend.turns, [])                  # and never go to the agent
        finally:
            h.finish()

    def test_next_greeting_is_prepared_when_idle(self):
        h = PipelineHarness(open_calculator)
        h.cfg["greeting"]["prepare_after_idle_s"] = 0
        greeter = FakeGreeter()
        h.components["greeter"] = greeter
        h.start()
        try:
            self.assertTrue(wait_for(lambda: greeter.prepared >= 1))
        finally:
            h.finish()


class TypedAndControlTests(unittest.TestCase):
    def test_typed_command_and_queueing(self):
        def slow(turn, ctx, ex):
            time.sleep(0.3)
            return open_calculator(turn, ctx, ex)
        h = PipelineHarness(slow).start()
        try:
            h.commands.put("open calculator")
            h.commands.put("open calculator again")
            self.assertTrue(wait_for(lambda: len(h.of("timing")) == 2, 10))
            self.assertEqual(len(h.backend.turns), 2)   # sequential, never overlapping
            self.assertEqual([e["source"] for e in h.of("transcript")], ["text", "text"])
        finally:
            h.finish()

    def test_conversational_reply_without_tools(self):
        h = PipelineHarness(lambda t, c, e: AgentResult(reply="Paris.")).start()
        try:
            h.commands.put("capital of france?")
            self.assertTrue(h.wait_idle_after("timing"))
            self.assertEqual(h.tts.spoken, ["Paris."])
            self.assertEqual(h.of("tool_call"), [])
            self.assertEqual(h.of("action"), [])                                # no legacy action/result events
        finally:
            h.finish()

    def test_agent_error_is_reported_spoken_and_recovers(self):
        h = PipelineHarness(lambda t, c, e: AgentResult(reply="", ok=False, error="Claude Code error: Not logged in")).start()
        try:
            h.commands.put("hello")
            self.assertTrue(wait_for(lambda: h.has("error", stage="llm")))
            self.assertTrue(wait_for(lambda: h.phases()[-1] == "idle"))
            self.assertEqual(h.tts.spoken, ["Sorry, I couldn't reach Claude"])
            self.assertEqual(h.memory.recent_messages()[-1]["role"], "error")
        finally:
            h.finish()

    def test_cancel_during_agent_run(self):
        started = threading.Event()

        def blocking(turn, ctx, ex):
            started.set()
            ctx.cancel_event.wait(5)
            return AgentResult(reply="", ok=False, cancelled=True, error="Cancelled")
        h = PipelineHarness(blocking).start()
        try:
            h.commands.put("do something long")
            self.assertTrue(started.wait(5))
            h.cancel.set()
            self.assertTrue(wait_for(lambda: h.has("cancelled")))
            self.assertTrue(wait_for(lambda: h.phases()[-1] == "idle"))
            self.assertFalse(h.cancel.is_set(), "cancel must be cleared on return to idle")
            self.assertEqual(h.tts.spoken, [])
        finally:
            h.finish()

    def test_approval_by_typed_yes_runs_tool(self):
        mem_id = {}

        def forget(turn, ctx, ex):
            res = ex.execute("forget", {"memory_id": mem_id["id"]}, ctx)
            return AgentResult(reply="Deleted." if res.ok else res.content)
        h = PipelineHarness(forget)
        mem_id["id"] = h.memory.add_memory("temporary fact")["id"]
        h.start()
        try:
            h.commands.put("forget that")
            self.assertTrue(wait_for(lambda: h.has("approval_request")))
            self.assertIn("wants to delete memory", h.tts.spoken[0])    # asked aloud
            h.commands.put("yes")                                         # typed answer (queue-based UIs)
            self.assertTrue(h.wait_idle_after("timing"))
            self.assertTrue(h.has("approval_resolved", decision="allow"))
            self.assertIsNone(h.memory.get_memory(mem_id["id"]))
        finally:
            h.finish()

    def test_approval_denied_runs_nothing(self):
        mem_id = {}

        def forget(turn, ctx, ex):
            res = ex.execute("forget", {"memory_id": mem_id["id"]}, ctx)
            return AgentResult(reply=res.content)
        h = PipelineHarness(forget)
        mem_id["id"] = h.memory.add_memory("keep me")["id"]
        h.start()
        try:
            h.commands.put("forget that")
            self.assertTrue(wait_for(lambda: h.broker.pending is not None))
            h.broker.resolve(None, False, via="test button")
            self.assertTrue(h.wait_idle_after("timing"))
            self.assertIsNotNone(h.memory.get_memory(mem_id["id"]))
            self.assertTrue(h.has("tool_result", status="denied"))
        finally:
            h.finish()

    def test_missing_microphone_degrades_to_text_only(self):
        class NoMic:
            sample_rate, chunk_size = 16000, 512

            def stream(self):
                raise RuntimeError("Error querying device 99")
        h = PipelineHarness(lambda t, c, e: AgentResult(reply="Typed still works."))
        h.components["audio"] = NoMic()
        h.start()
        try:
            ready = h.of("ready")[0]
            self.assertFalse(ready["voice"])
            self.assertTrue(h.has("error", stage="voice"))
            h.commands.put("hello")
            self.assertTrue(h.wait_idle_after("timing"))
            self.assertEqual(h.tts.spoken, ["Typed still works."])
        finally:
            h.finish()
        self.assertEqual(h.exit_code, 0)

    def test_gui_task_commands_reach_the_orchestrator(self):
        h = PipelineHarness(lambda t, c, e: AgentResult(reply="Continuing."))
        task = h.memory.create_task("Deploy the demo", project="demo")
        h.memory.update_task(task["id"], status="BLOCKED", blocked_reason="Needed a version number")
        h.start()
        try:
            h.commands.put({"type": "task", "action": "resume", "task_id": task["id"], "answer": "use 1.0.0"})
            self.assertTrue(h.wait_idle_after("timing"))
            self.assertTrue(h.has("transcript", text=f"Resume task #{task['id']}"))
            self.assertTrue(any("The user resumed this task" in t and "use 1.0.0" in t for t in h.backend.turns))
            self.assertTrue(any(e["kind"] == "resumed" for e in h.memory.task_events(task["id"])))
            self.assertTrue(h.of("task_update"))
        finally:
            h.finish()


if __name__ == "__main__":
    unittest.main()
