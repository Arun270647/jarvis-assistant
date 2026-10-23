"""Test doubles for driving the real pipeline without a microphone, models or speakers.

Audio is a scripted sequence of 512-sample chunks: silence (0) or speech (1000).
The fake VAD fires on any non-silent chunk. FakeTTS "records" its own voice back
into the stream buffer (like a real mic hearing the speakers), so the tests can
prove the pipeline drains it instead of transcribing itself.
"""
import collections
import json
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHUNK = 512
SILENCE, SPEECH = 0, 1000


def chunk(level: int) -> bytes:
    return np.full(CHUNK, level, dtype=np.int16).tobytes()


class FakeStream:
    def __init__(self, script=None):
        self.pending = collections.deque()      # audio already captured (read_available)
        self.script = collections.deque(script or [])  # audio that "arrives" as we read
        self.lock = threading.Lock()
        self.reads = 0

    def add_script(self, levels):
        with self.lock:
            self.script.extend(levels)

    def echo(self, levels):
        with self.lock:
            self.pending.extend(levels)

    @property
    def read_available(self):
        with self.lock:
            return len(self.pending) * CHUNK

    def read(self, n):
        with self.lock:
            self.reads += 1
            if self.pending:
                level = self.pending.popleft()
            elif self.script:
                level = self.script.popleft()
            else:
                level = None
        if level is None:
            time.sleep(0.002)
            level = SILENCE
        return chunk(level), False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAudio:
    sample_rate = 16000
    chunk_size = CHUNK

    def __init__(self, stream: FakeStream):
        self._stream = stream

    def stream(self):
        return self._stream


def fake_vad(audio_int16) -> bool:
    return int(np.abs(audio_int16).mean()) >= SPEECH


class FakeTranscriber:
    def __init__(self, texts):
        self.texts = collections.deque(texts)
        self.calls = 0

    def transcribe_audio(self, audio):
        self.calls += 1
        return self.texts.popleft() if self.texts else ""


class FakeTTS:
    """Records what was spoken and pushes an 'echo' of it (speech-like audio) into the mic."""

    def __init__(self, stream: FakeStream = None, echo_chunks: int = 20, delay: float = 0.0):
        self.spoken = []
        self.stream = stream
        self.echo_chunks = echo_chunks
        self.delay = delay

    def speak(self, text, blocking=True):
        self.spoken.append(text)
        if self.stream is not None:
            self.stream.echo([SPEECH] * self.echo_chunks)
        if self.delay:
            time.sleep(self.delay)
        return {"synthesis_time": 0.001, "playback_time": 0.001}


class FakeGreeter:
    """Stands in for GreetingPool (no Claude CLI): hands out numbered greetings."""

    def __init__(self):
        self.taken = 0
        self.prepared = 0

    def take(self):
        self.taken += 1
        return f"Hey, greeting number {self.taken}."

    needs_prepare = True

    def prepare_next(self):
        self.prepared += 1
        return True

    def refill_async(self):
        pass


class ScriptedBackend:
    """Agent backend whose behaviour is a function(text, ctx, executor) -> AgentResult."""

    name = "fake"
    model = "fake-model"

    def __init__(self, executor, behaviour):
        self.executor = executor
        self.behaviour = behaviour
        self.loaded = self.unloaded = False
        self.turns = []

    def load(self):
        self.loaded = True

    def run(self, user_turn, ctx, emit):
        self.turns.append(user_turn)
        return self.behaviour(user_turn, ctx, self.executor)

    def cancel(self):
        pass

    def reset(self):
        pass

    def unload(self):
        self.unloaded = True


def temp_db() -> Path:
    return Path(tempfile.mkdtemp(prefix="jarvis-test-")) / "test.db"


def fixture_profile(root: Path) -> dict:
    """The fake user "Alex" (tests/fixtures/user_profile.test.json), with the project folders created under root."""
    text = (FIXTURES / "user_profile.test.json").read_text(encoding="utf-8")
    profile = json.loads(text.replace("{root}", Path(root).as_posix()))
    for project in profile.get("projects", {}).values():
        Path(project["path"]).mkdir(parents=True, exist_ok=True)
    return profile


def test_config(base: dict) -> dict:
    """Real config with deterministic pipeline timings and dry-run on."""
    import copy
    cfg = copy.deepcopy(base)
    cfg["dry_run"] = True
    cfg.setdefault("pipeline", {}).update({
        "silence_timeout_ms": 160, "early_bail_ms": 600, "max_recording_ms": 3000,
    })
    cfg["greeting"] = dict(cfg.get("greeting", {}), enabled=False)    # never call the real Claude CLI
    cfg.setdefault("tts", {})["enabled"] = True
    cfg.setdefault("tools", {}).update({"approval_timeout_ms": 4000, "permissions": {}})
    cfg.setdefault("voice_approval", {})["window_ms"] = 300
    return cfg


def wait_for(pred, timeout=10.0, interval=0.01) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False
