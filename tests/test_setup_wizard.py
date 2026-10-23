"""The setup wizard's backend (src/app/setup.py): what each screen saves, the permission presets, resuming,
and reading the Claude Code sign-in status. The screens themselves are src/gui/web/setup.js."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.app import setup
from src.core import config_store, paths

FACTS = {"nvidia_gpu": False, "cpu_logical_cores": 16, "nvidia_free_vram_gb": 0.0, "cuda_libs": False}


class SetupWizardTests(unittest.TestCase):
    def setUp(self):
        self.config = paths.config_file()
        self.profile = paths.profile_file()
        self.saved = {p: p.read_text(encoding="utf-8") if p.exists() else None for p in (self.config, self.profile)}
        self.addCleanup(self.restore)
        if self.config.exists():
            self.config.unlink()

    def restore(self):
        for p, text in self.saved.items():
            if text is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(text, encoding="utf-8")

    def cfg(self):
        return config_store.load_config()

    def test_state_for_a_new_install(self):
        st = setup.state(facts=FACTS)
        self.assertTrue(st["needed"])
        self.assertEqual((st["step"], st["name"]), ("name", "Jarvis"))
        self.assertEqual(st["steps"][0], "name")
        stt = next(g for g in st["groups"] if g["id"] == "stt")
        self.assertEqual((stt["recommended"], stt["selected"]), ("whisper-small", "whisper-small"))
        self.assertTrue(any(o["recommended"] for o in stt["options"]))
        self.assertNotIn("{name}", json.dumps(st["groups"]))
        self.assertEqual([p["id"] for p in st["presets"]], ["everyday", "work", "full"])

    def test_name_screen(self):
        self.assertFalse(setup.save_step("name", {"name": "R2"})["ok"])
        self.assertTrue(setup.save_step("name", {"name": "  Nova  "})["ok"])
        self.assertEqual(config_store.assistant_name(self.cfg()), "Nova")
        self.assertEqual(self.cfg()["setup"]["step"], "name")
        texts = json.dumps(setup.state(facts=FACTS)["groups"])
        self.assertIn("so Nova knows", texts)

    def test_models_screen_saves_settings_and_lists_downloads(self):
        r = setup.save_step("models", {"choices": {"vad": "silero-vad", "stt": "whisper-medium", "tts": "system"}}, FACTS)
        self.assertTrue(r["ok"], r)
        cfg = self.cfg()
        self.assertEqual((cfg["stt"]["model_path"], cfg["stt"]["device"]), ("medium", "cpu"))
        self.assertEqual(cfg["tts"]["engine"], "system")
        self.assertEqual(sorted(r["downloads"]), ["silero-vad", "whisper-medium"], "the built-in voice downloads nothing")
        stt = next(g for g in setup.state(facts=FACTS)["groups"] if g["id"] == "stt")
        self.assertEqual(stt["selected"], "whisper-medium", "the choice is remembered")

    def test_microphone_and_voice_screens(self):
        setup.save_step("microphone", {"device": "3"})
        self.assertEqual(self.cfg()["audio"]["input_device_index"], 3)
        setup.save_step("microphone", {"device": None})
        self.assertIsNone(self.cfg()["audio"]["input_device_index"])
        r = setup.save_step("voice", {"option": "piper-en_GB-alba-medium", "speak": False}, FACTS)
        cfg = self.cfg()
        self.assertEqual((cfg["tts"]["engine"], cfg["tts"]["piper"]["voice"], cfg["tts"]["enabled"]),
                         ("piper", "en_GB-alba-medium", False))
        self.assertEqual(r["downloads"], ["piper-en_GB-alba-medium"])

    def test_about_you_writes_the_profile(self):
        folder = Path(tempfile.mkdtemp(prefix="Shop Site "))
        before = config_store.load_profile()
        r = setup.save_step("about", {"user_name": "Sam", "folders": [str(folder), str(folder), "C:/does/not/exist"]})
        self.assertTrue(r["ok"], r)
        profile = config_store.load_profile()
        self.assertEqual(profile["user"]["name"], "Sam")
        added = [p for k, p in profile["projects"].items() if k not in before.get("projects", {})]
        self.assertEqual([p["path"] for p in added], [str(folder.resolve())], "once, and only folders that exist")
        self.assertIn("calculator", profile["apps"], "the shipped profile is still underneath")

    def test_permission_presets(self):
        setup.save_step("permissions", {"preset": "everyday"})
        perms = self.cfg()["tools"]["permissions"]
        self.assertEqual(perms["run_command"], "deny")
        self.assertEqual(perms["desktop_click"], "deny")
        self.assertNotIn("open_app", perms)
        setup.save_step("permissions", {"preset": "work"})
        perms = self.cfg()["tools"]["permissions"]
        self.assertNotIn("run_command", perms, "work allows project commands again")
        self.assertEqual(perms["desktop_click"], "deny")
        config_store.update_config({"tools.permissions": dict(perms, open_app="ask")})
        setup.save_step("permissions", {"preset": "full"})
        self.assertEqual(self.cfg()["tools"]["permissions"], {"open_app": "ask"}, "the user's own choices stay")
        self.assertEqual(setup.current_preset(self.cfg()), "full")
        self.assertFalse(setup.save_step("permissions", {"preset": "root"})["ok"])

    def test_startup_and_finish(self):
        with mock.patch("src.core.startup.set_startup") as set_startup:
            setup.save_step("startup", {"autostart": True})
        set_startup.assert_called_once_with(True)
        self.assertTrue(setup.needed())
        setup.save_step("finish", {})
        self.assertFalse(setup.needed())
        self.assertFalse(setup.save_step("nonsense", {})["ok"])

    def test_settings_models_use_this(self):
        r = setup.use_model("whisper-base", FACTS)
        self.assertEqual(r, {"ok": True, "download": True})
        cfg = self.cfg()
        self.assertEqual((cfg["stt"]["model_path"], cfg["setup"]["models"]["stt"]), ("base", "whisper-base"))
        self.assertEqual(setup.use_model("system", FACTS), {"ok": True, "download": False})
        self.assertEqual(self.cfg()["tts"]["engine"], "system")
        self.assertEqual(self.cfg()["setup"]["step"], "", "Settings doesn't move the wizard")

    def test_resumes_after_the_last_saved_screen(self):
        setup.save_step("name", {"name": "Echo"})
        setup.save_step("microphone", {"device": None})
        self.assertEqual(setup.state(facts=FACTS)["step"], "microphone")


class ClaudeSignInTests(unittest.TestCase):
    def test_status_from_the_cli(self):
        out = json.dumps({"loggedIn": True, "email": "sam@example.com", "subscriptionType": "max", "authMethod": "claude.ai"})
        with mock.patch("src.core.winproc.run_hidden", return_value=subprocess.CompletedProcess([], 0, out, "")):
            st = setup.claude_status("claude")
        self.assertEqual(st, {"installed": True, "signed_in": True, "email": "sam@example.com", "plan": "max",
                              "method": "claude.ai"})
        with mock.patch("src.core.winproc.run_hidden", return_value=subprocess.CompletedProcess([], 1, '{"loggedIn": false}', "")):
            self.assertFalse(setup.claude_status("claude")["signed_in"])
        with mock.patch.object(setup, "find_claude", return_value=None):
            self.assertEqual(setup.claude_status(), {"installed": False, "signed_in": False})

    def test_login_runs_hidden(self):
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            setup.start_claude_login("C:/x/claude.exe")
        popen.assert_called_once_with(["C:/x/claude.exe", "auth", "login", "--claudeai"])
        with mock.patch.object(setup, "find_claude", return_value=None), self.assertRaises(FileNotFoundError):
            setup.start_claude_login()


if __name__ == "__main__":
    unittest.main()
