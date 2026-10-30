"""Fish Audio voice engine (src/tts/fish.py): the cloud API with a fake opener, and local fish-speech with a fake
server that speaks the same HTTP API (no torch, no model, no network, no audio device)."""
import base64
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import time
import types
import unittest
import urllib.error
import wave
from pathlib import Path
from unittest import mock

try:
    import sounddevice  # noqa: F401
except (ImportError, OSError):                       # no PortAudio on this machine: the engine never plays here
    sys.modules["sounddevice"] = types.SimpleNamespace(play=lambda *a, **k: None, stop=lambda: None)

import src.tts.fish as fish  # noqa: E402
from src.core import config_store, models  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_fish  # noqa: E402


def wav_bytes(samples=80, sr=44100, streamed=False) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x01\x00" * samples)
    data = bytearray(buf.getvalue())
    if streamed:                                     # what a chunked answer's header looks like: no real sizes
        data[4:8] = struct.pack("<I", 0xFFFFFFFF)
        data[40:44] = struct.pack("<I", 0xFFFFFFFF)
    return bytes(data)


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeFishCloud:
    """Stands in for urllib.request.urlopen: records each request, answers WAV, or fails on "FAILME"."""

    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data)
        self.requests.append({"url": req.full_url, "headers": dict(req.header_items()), "body": body})
        if "FAILME" in body["text"]:
            raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {},
                                         io.BytesIO(b'{"message": "No credit left"}'))
        return FakeResponse(wav_bytes(streamed=True))


class FakeVoice:
    def __init__(self):
        self.spoken = []

    def speak(self, text, blocking=True):
        self.spoken.append(text)
        return {"engine": "fallback"}


FAKE_SERVER = textwrap.dedent('''
    import json, os, sys, wave, io
    import socketserver
    from http.server import BaseHTTPRequestHandler
    host, port = sys.argv[sys.argv.index("--listen") + 1].split(":")
    log = os.environ["FAKE_FISH_LOG"]
    with open(log + ".args", "w") as f:
        json.dump(sys.argv[1:], f)
    def wav():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\\x02\\x00" * 50)
        return buf.getvalue()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200 if self.path == "/v1/health" else 404); self.end_headers(); self.wfile.write(b'{"status": "ok"}')
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with open(log, "a") as f:
                f.write(json.dumps(body) + "\\n")
            data = wav()
            self.send_response(200); self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    # TCPServer, not HTTPServer: HTTPServer looks up the host's full name, which can stall for a minute on macOS
    socketserver.TCPServer((host, int(port)), H).serve_forever()
''')


class FishEngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jarvis-fish-"))
        self._saved = (fish.CACHE_DIR, fish.sd.play, fish.sd.stop)
        fish.CACHE_DIR = self.dir / "cache"
        self.played = []
        fish.sd.play = lambda audio, samplerate, blocking=True: self.played.append((len(audio), samplerate))
        fish.sd.stop = lambda: None
        self.engines = []
        patcher = mock.patch.object(fish, "api_key", return_value=("test-key", "saved"))
        self.key = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for e in self.engines:
            e.close()
        fish.CACHE_DIR, fish.sd.play, fish.sd.stop = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def engine(self, fallback=None, opener=None, **extra):
        cfg = {"tts": {"fish": dict({"mode": "cloud", "voice_id": fish.DEFAULT_VOICE_ID, "cloud_model": "s2.1-pro",
                                     "cache_phrases": []}, **extra)}}
        e = fish.FishTTS(cfg, fallback=fallback, opener=opener or FakeFishCloud())
        self.engines.append(e)
        return e

    # ------------------------------------------------------------------ cloud

    def test_cloud_request_uses_the_voice_id_key_and_model(self):
        cloud = FakeFishCloud()
        e = self.engine(opener=cloud)
        self.assertTrue(e.ready)
        res = e.speak("Good evening. Systems are online.")
        self.assertEqual(res["engine"], "fish")
        self.assertEqual([p[1] for p in self.played], [44100, 44100])
        self.assertEqual([r["body"]["text"] for r in cloud.requests], ["Good evening.", "Systems are online."])
        first = cloud.requests[0]
        self.assertEqual(first["url"], "https://api.fish.audio/v1/tts")
        self.assertEqual(first["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(first["headers"]["Model"], "s2.1-pro")
        self.assertEqual(first["body"]["reference_id"], "29c31f6e158c4c8f84c14f9f3d8ba008")
        self.assertEqual(first["body"]["format"], "wav")
        self.assertNotIn("references", first["body"])

    def test_without_a_key_the_fallback_speaks(self):
        self.key.return_value = (None, None)
        backup = FakeVoice()
        e = self.engine(fallback=backup)
        self.assertFalse(e.ready)
        self.assertIn("API key", e._failed)
        self.assertEqual(e.speak("Hello there."), {"engine": "fallback"})
        self.assertEqual(backup.spoken, ["Hello there."])
        self.assertRaises(RuntimeError, e.synthesize, "Hello.")

    def test_a_failed_sentence_hands_the_rest_to_the_fallback(self):
        backup = FakeVoice()
        e = self.engine(fallback=backup)
        e.speak("One. FAILME two. Three.")
        self.assertEqual(len(self.played), 1)
        self.assertEqual(backup.spoken, ["FAILME two. Three."])

    def test_http_errors_are_plain_sentences(self):
        e = self.engine()
        with self.assertRaises(RuntimeError) as ctx:
            e._request("FAILME")
        self.assertEqual(str(ctx.exception), "Fish Audio: the Fish Audio account is out of credit (No credit left)")

    def test_prepared_text_plays_from_the_cache(self):
        cloud = FakeFishCloud()
        e = self.engine(opener=cloud)
        e.prepare("Welcome back.")
        end = time.time() + 5
        while not list(fish.CACHE_DIR.glob("*.wav")) and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(len(cloud.requests), 1)
        e.speak("Welcome back.")
        self.assertEqual(len(cloud.requests), 1, "no second request for cached audio")
        self.assertEqual(len(self.played), 1)
        e.discard("Welcome back.")
        self.assertEqual(list(fish.CACHE_DIR.glob("*.wav")), [])

    def test_synthesize_returns_pcm(self):
        audio = self.engine().synthesize("Hello. World.")
        self.assertEqual((audio.dtype.name, len(audio)), ("int16", 160))

    def test_parse_wav_reads_streamed_and_stereo_audio(self):
        audio, sr = fish.parse_wav(wav_bytes(samples=10, sr=24000, streamed=True))
        self.assertEqual((len(audio), sr), (10, 24000))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x02\x00\x04\x00" * 5)
        audio, _ = fish.parse_wav(buf.getvalue())
        self.assertEqual(audio.tolist(), [3] * 5)
        self.assertRaises(ValueError, fish.parse_wav, b'{"message": "oops"}')

    # ------------------------------------------------------------------ local fish-speech

    def local_setup(self):
        repo = self.dir / "fish-speech"
        (repo / "tools").mkdir(parents=True)
        (repo / "tools" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tools" / "api_server.py").write_text(FAKE_SERVER, encoding="utf-8")
        ckpt = self.dir / "checkpoints" / "s1-mini"
        ckpt.mkdir(parents=True)
        clip = self.dir / "reference.wav"
        clip.write_bytes(wav_bytes(samples=400))
        return {"mode": "local", "python": sys.executable, "repo": str(repo), "checkpoint": str(ckpt), "device": "cpu",
                "reference_audio": str(clip), "reference_text": "Allow me to introduce myself.",
                "startup_timeout_s": 60, "sentence_timeout_s": 10}

    def test_local_fish_speech_clones_the_reference_clip(self):
        local = self.local_setup()
        log = self.dir / "requests.jsonl"
        with mock.patch.dict(os.environ, {"FAKE_FISH_LOG": str(log)}):
            cfg = {"tts": {"fish": dict(local, cache_phrases=[])}}
            e = fish.FishTTS(cfg)                    # its own opener: no proxy for 127.0.0.1
            self.engines.append(e)
            audio = e.synthesize("Hello. World.")
        self.assertEqual(len(audio), 100)
        args = json.loads(Path(str(log) + ".args").read_text())
        self.assertEqual(args[args.index("--llama-checkpoint-path") + 1], local["checkpoint"])
        self.assertEqual(args[args.index("--device") + 1], "cpu")
        self.assertNotIn("--half", args)
        body = json.loads(log.read_text().splitlines()[0])
        self.assertEqual(body["text"], "Hello.")
        self.assertEqual(body["references"][0]["text"], "Allow me to introduce myself.")
        self.assertEqual(base64.b64decode(body["references"][0]["audio"]), Path(local["reference_audio"]).read_bytes())
        self.assertNotIn("reference_id", body)
        e.close()
        self.assertIsNone(e._proc)

    def test_local_mode_without_a_clip_uses_the_fallback(self):
        local = self.local_setup()
        local["reference_audio"] = ""
        backup = FakeVoice()
        e = self.engine(fallback=backup, **local)
        end = time.time() + 5
        while not e._failed and time.time() < end:
            time.sleep(0.02)
        self.assertIn("clip to clone", e._failed)
        e.speak("Hello.")
        self.assertEqual(backup.spoken, ["Hello."])


class FishSettingsTests(unittest.TestCase):
    def test_fish_voice_is_listed_first_in_the_wizard(self):
        first = models.group("tts")["options"][0]
        self.assertEqual(first["id"], "fish-jarvis")
        self.assertEqual(first["needs_key"], "fish")
        self.assertEqual(models.status(first)["state"], "builtin", "nothing to download")
        self.assertEqual(config_store.validate_update(first["config"]), [])
        self.assertIn("29c31f6e158c4c8f84c14f9f3d8ba008", first["page"])

    def test_wizard_shows_whether_the_key_is_saved(self):
        from src.app import setup
        facts = {"os": "windows", "gpu": None, "vram_gb": 0, "ram_gb": 16, "cpu_cores": 8}
        from src.core import fish_key
        with mock.patch.object(fish_key, "api_key", return_value=("k", "saved")):
            tts = next(g for g in setup.state(facts=facts)["groups"] if g["id"] == "tts")
        self.assertEqual(tts["options"][0]["id"], "fish-jarvis")
        self.assertTrue(tts["options"][0]["key_saved"])
        self.assertEqual((tts["recommended"], tts["selected"]), ("fish-jarvis", "fish-jarvis"), "preselected")
        self.assertFalse(tts["options"][1]["key_saved"])

    def test_defaults_and_engine_choice(self):
        defaults = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))["tts"]["fish"]
        self.assertEqual((defaults["mode"], defaults["voice_id"], defaults["fallback"]),
                         ("cloud", fish.DEFAULT_VOICE_ID, "kokoro"))
        self.assertEqual(config_store.validate_update({"tts.engine": "fish", "tts.fish.mode": "local",
                                                       "tts.fish.cloud_model": "s2.1-pro-free"}), [])
        self.assertNotEqual(config_store.validate_update({"tts.fish.mode": "somewhere"}), [])

    def test_build_engine_with_no_fallback(self):
        from src.tts import build_engine
        with mock.patch.object(fish, "api_key", return_value=(None, None)):
            e = build_engine("fish", {"tts": {"fish": {"mode": "cloud", "fallback": "none", "cache_phrases": []}}})
        self.assertIsNone(e.fallback)
        self.assertFalse(e.ready)


class SetupFishTests(unittest.TestCase):
    def test_steps(self):
        venv = setup_fish.folder() / "venv"
        steps = setup_fish.commands("C:/Python311/python.exe", venv, cuda=True, model="s1-mini")
        joined = [" ".join(s) for s in steps]
        self.assertEqual(steps[0], ["C:/Python311/python.exe", "-m", "venv", str(venv)])
        self.assertTrue(any("download.pytorch.org/whl/cu128" in s for s in joined), "CUDA torch before fish-speech")
        self.assertIn(str(setup_fish.repo_dir()), joined[-3])
        self.assertIn("fishaudio/s1-mini", joined[-1])
        self.assertFalse(any("cu128" in s for s in map(" ".join, setup_fish.commands("py", venv, False, "s2-pro"))))
        self.assertTrue(setup_fish.SOURCE.endswith(setup_fish.COMMIT + ".zip"))

    def test_settings_are_valid_and_point_into_the_models_folder(self):
        updates = setup_fish.settings(setup_fish.folder() / "venv", cuda=False, model="s1-mini",
                                      reference=str(setup_fish.folder() / "reference.wav"), reference_text="Hi.", use=True)
        self.assertEqual(config_store.validate_update(updates), [])
        self.assertEqual((updates["tts.engine"], updates["tts.fish.mode"], updates["tts.fish.device"]),
                         ("fish", "local", "cpu"))
        self.assertTrue(updates["tts.fish.checkpoint"].endswith("s1-mini"))

    def test_download_source_drops_the_top_folder_and_refuses_escapes(self):
        import zipfile
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-fish-src-"))
        self.addCleanup(shutil.rmtree, tmp, True)

        def archive(names):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for n in names:
                    z.writestr(n, "x")
            return FakeResponse(buf.getvalue())

        with mock.patch.object(setup_fish.urllib.request, "urlopen",
                               return_value=archive(["fish-speech-abc/tools/api_server.py", "fish-speech-abc/README.md"])):
            setup_fish.download_source(tmp / "fish-speech")
        self.assertTrue((tmp / "fish-speech" / "tools" / "api_server.py").is_file())
        with mock.patch.object(setup_fish.urllib.request, "urlopen", return_value=archive(["top/../../evil.py"])):
            self.assertRaises(SystemExit, setup_fish.download_source, tmp / "other")
        self.assertFalse((tmp / "evil.py").exists())

    def test_dry_run_changes_nothing(self):
        before = config_store.load_config()["tts"]["fish"]
        with mock.patch.object(setup_fish, "find_python", return_value="py"), \
                mock.patch("builtins.print"):
            self.assertEqual(setup_fish.main(["--dry-run"]), 0)
        self.assertEqual(config_store.load_config()["tts"]["fish"], before)


if __name__ == "__main__":
    unittest.main()
