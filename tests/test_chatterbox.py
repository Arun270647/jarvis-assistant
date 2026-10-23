"""Chatterbox voice engine with a fake worker (no model, no audio device).

The fake worker speaks the same JSON-lines protocol as src/tts/chatterbox_worker.py and writes
short tone WAVs, so ordering, the phrase cache, stop/cancel and the Piper fallback are tested
without torch or Chatterbox.
"""
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import numpy as np

import src.tts.chatterbox as cb

FAKE_WORKER = textwrap.dedent('''
    import json, os, sys, threading, queue, time, wave
    delay = float(os.environ.get("FAKE_DELAY", "0.05"))
    if os.environ.get("FAKE_FAIL_LOAD"):
        print(json.dumps({"ready": False, "error": "no model"}), flush=True); sys.exit(1)
    jobs, cutoff = queue.Queue(), [0]
    def rd():
        for line in sys.stdin:
            m = json.loads(line)
            if m["op"] == "cancel_all": cutoff[0] = max(cutoff[0], m["upto"])
            else: jobs.put(m)
        jobs.put(None)
    threading.Thread(target=rd, daemon=True).start()
    print(json.dumps({"ready": True, "sr": 8000}), flush=True)
    while True:
        j = jobs.get()
        if j is None: break
        if j["id"] <= cutoff[0]:
            print(json.dumps({"id": j["id"], "cancelled": True}), flush=True); continue
        time.sleep(delay)
        if "FAILME" in j["text"]:
            print(json.dumps({"id": j["id"], "ok": False, "error": "boom"}), flush=True); continue
        with wave.open(j["out"], "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes((b"\\x01\\x00" * 80))
        print(json.dumps({"id": j["id"], "ok": True, "path": j["out"], "ms": 1, "dur": 0.01}), flush=True)
''')


class FakePiper:
    def __init__(self):
        self.spoken = []

    def speak(self, text, blocking=True):
        self.spoken.append(text)
        return {"engine": "piper"}


class ChatterboxEngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jarvis-cb-"))
        (self.dir / "worker.py").write_text(FAKE_WORKER, encoding="utf-8")
        self._saved = (cb.WORKER, cb.CACHE_DIR, cb.TMP_DIR, cb.sd.play, cb.sd.stop)
        cb.WORKER, cb.CACHE_DIR, cb.TMP_DIR = self.dir / "worker.py", self.dir / "cache", self.dir / "tmp"
        self.played = []
        cb.sd.play = lambda audio, samplerate, blocking=True: self.played.append((len(audio), samplerate))
        cb.sd.stop = lambda: None
        self.engines = []

    def tearDown(self):
        for e in self.engines:
            e.close()
        cb.WORKER, cb.CACHE_DIR, cb.TMP_DIR, cb.sd.play, cb.sd.stop = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def engine(self, fallback=None, **extra):
        cfg = {"tts": {"chatterbox": dict({"python": sys.executable, "startup_timeout_s": 20, "sentence_timeout_s": 10,
                                           "cache_phrases": ["Ready when you are."]}, **extra)}}
        e = cb.ChatterboxTTS(cfg, fallback=fallback)
        self.engines.append(e)
        return e

    def wait_ready(self, e):
        end = time.time() + 20
        while not e.ready and time.time() < end and not e._failed:
            time.sleep(0.05)
        self.assertTrue(e.ready, e._failed)

    def test_split_sentences(self):
        self.assertEqual(cb.split_sentences("Hi there. How are you? Fine!"), ["Hi there.", "How are you?", "Fine!"])
        long = "word, " * 120
        self.assertTrue(all(len(p) <= 281 for p in cb.split_sentences(long)))

    def test_speaks_every_sentence_in_order(self):
        e = self.engine()
        self.wait_ready(e)
        res = e.speak("One. Two. Three.")
        self.assertEqual(len(self.played), 3)
        self.assertEqual(self.played[0][1], 8000)
        self.assertEqual(res["engine"], "chatterbox")
        self.assertEqual(list(cb.TMP_DIR.glob("*.wav")), [])          # temp files cleaned up

    def test_fixed_phrase_is_cached_and_reused(self):
        e = self.engine()
        self.wait_ready(e)
        end = time.time() + 10
        while not list(cb.CACHE_DIR.glob("*.wav")) and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(len(list(cb.CACHE_DIR.glob("*.wav"))), 1)
        before = e._next_id
        e.speak("Ready when you are.")
        self.assertEqual(e._next_id, before)                          # no new generation
        self.assertEqual(len(self.played), 1)

    def test_synthesize_returns_pcm(self):
        e = self.engine()
        audio = e.synthesize("Hello. World.")
        self.assertEqual(audio.dtype, np.int16)
        self.assertEqual(len(audio), 160)

    def test_falls_back_to_piper_when_load_fails(self):
        import os
        os.environ["FAKE_FAIL_LOAD"] = "1"
        try:
            piper = FakePiper()
            e = self.engine(fallback=piper)
            end = time.time() + 10
            while not e._failed and time.time() < end:
                time.sleep(0.05)
            e.speak("Hello there.")
            self.assertEqual(piper.spoken, ["Hello there."])
        finally:
            del os.environ["FAKE_FAIL_LOAD"]

    def test_failed_sentence_hands_the_rest_to_piper(self):
        piper = FakePiper()
        e = self.engine(fallback=piper)
        self.wait_ready(e)
        e.speak("First. FAILME now. Last.")
        self.assertEqual(len(self.played), 1)
        self.assertEqual(piper.spoken, ["FAILME now. Last."])

    def test_missing_python_uses_piper(self):
        piper = FakePiper()
        e = self.engine(fallback=piper, python=str(self.dir / "nope.exe"))
        time.sleep(0.3)
        e.speak("Hi.")
        self.assertEqual(piper.spoken, ["Hi."])

    def test_prepare_caches_a_one_off_text_and_discard_removes_it(self):
        e = self.engine()
        self.wait_ready(e)
        e.prepare("Evening, Alex. Let's cook.")
        end = time.time() + 10
        while len(list(cb.CACHE_DIR.glob("*.wav"))) < 3 and time.time() < end:   # fixed phrase + 2 sentences
            time.sleep(0.05)
        before = e._next_id
        e.speak("Evening, Alex. Let's cook.")
        self.assertEqual(e._next_id, before)                          # played from the cache
        e.discard("Evening, Alex. Let's cook.")
        self.assertEqual(len(list(cb.CACHE_DIR.glob("*.wav"))), 1)    # the fixed phrase stays

    def test_stop_drops_queued_sentences(self):
        import os
        os.environ["FAKE_DELAY"] = "0.3"
        try:
            e = self.engine()
            self.wait_ready(e)
            cb.sd.play = lambda audio, samplerate, blocking=True: (self.played.append(1), e.stop())
            e.speak("A. B. C. D. E.")
            self.assertEqual(len(self.played), 1)
        finally:
            del os.environ["FAKE_DELAY"]


if __name__ == "__main__":
    unittest.main()
