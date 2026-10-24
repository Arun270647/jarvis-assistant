"""Settings and profile: shipped defaults <- the user's files <- environment overrides; Settings writes
only what differs from the defaults (atomically); older user files are migrated."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core import config_store

_ABSENT = object()


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.json"

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_without_a_user_file_the_defaults_apply_and_nothing_is_written(self):
        self.assertEqual(config_store.load_config(self.path), config_store.defaults())
        self.assertFalse(self.path.exists())

    def test_user_values_override_defaults_key_by_key(self):
        self.path.write_text(json.dumps({"agent": {"max_turns": 7}}), encoding="utf-8")
        cfg = config_store.load_config(self.path)
        self.assertEqual(cfg["agent"]["max_turns"], 7)
        self.assertEqual(cfg["agent"]["max_seconds"], config_store.defaults()["agent"]["max_seconds"])

    def test_update_writes_only_what_differs_from_the_defaults(self):
        d = config_store.defaults()
        key = "llm.providers.claude_code.effort"
        cfg = config_store.update_config({key: "high", "pipeline.silence_timeout_ms": 600,
                                          "tools.permissions": {"open_app": "ask"},
                                          "agent.max_turns": d["agent"]["max_turns"]}, self.path)
        self.assertEqual(cfg["llm"]["providers"]["claude_code"]["effort"], "high")
        self.assertEqual(self.read(), {"llm": {"providers": {"claude_code": {"effort": "high"}}},
                                       "pipeline": {"silence_timeout_ms": 600},
                                       "tools": {"permissions": {"open_app": "ask"}},
                                       "config_version": config_store.CONFIG_VERSION})
        # Back to the default value: the key leaves the user's file.
        config_store.update_config({key: d["llm"]["providers"]["claude_code"]["effort"], "tools.permissions": {}},
                                   self.path)
        self.assertEqual(self.read(), {"pipeline": {"silence_timeout_ms": 600},
                                       "config_version": config_store.CONFIG_VERSION})

    def test_a_new_default_reaches_users_who_never_changed_that_setting(self):
        config_store.update_config({"pipeline.silence_timeout_ms": 600}, self.path)
        newer = config_store.defaults()
        newer["agent"]["max_turns"] = 99
        with mock.patch.object(config_store, "defaults", lambda: copy.deepcopy(newer)):
            cfg = config_store.load_config(self.path)
        self.assertEqual(cfg["agent"]["max_turns"], 99)
        self.assertEqual(cfg["pipeline"]["silence_timeout_ms"], 600)

    def test_invalid_updates_rejected_and_file_untouched(self):
        config_store.update_config({"agent.max_turns": 9}, self.path)
        original = self.path.read_text(encoding="utf-8")
        for bad in ({"llm.backend": "gpt"}, {"agent.max_turns": 0}, {"agent.max_turns": "5"},
                    {"tools.permissions": {"open_app": "maybe"}}, {"not.a.key": 1}, {"dry_run": "yes"},
                    {"audio.input_device_index": True}):
            with self.assertRaises(ValueError, msg=str(bad)):
                config_store.update_config(bad, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_microphone_can_go_back_to_the_system_default(self):
        config_store.update_config({"audio.input_device_index": 3}, self.path)
        self.assertEqual(self.read()["audio"], {"input_device_index": 3})
        cfg = config_store.update_config({"audio.input_device_index": None}, self.path)
        self.assertIsNone(cfg["audio"]["input_device_index"])
        self.assertNotIn("audio", self.read())                               # null is the default again
        with self.assertRaises(ValueError):
            config_store.update_config({"agent.max_turns": None}, self.path)    # null only where it means something

    def test_int_accepted_for_float_setting(self):
        with mock.patch.dict(config_store.EDITABLE, {"pipeline.vad_threshold": (float, (0.0, 1.0))}):
            cfg = config_store.update_config({"pipeline.vad_threshold": 1}, self.path)
        self.assertIsInstance(cfg["pipeline"]["vad_threshold"], float)

    def test_environment_overrides_win_and_are_never_saved(self):
        env = {"JARVIS__stt__device": "cuda", "JARVIS__DRY_RUN": "true", "JARVIS__agent__task__max_nudges": "5"}
        with mock.patch.dict(os.environ, env):
            cfg = config_store.load_config(self.path)
            config_store.update_config({"agent.max_turns": 9}, self.path)
        self.assertEqual((cfg["stt"]["device"], cfg["dry_run"], cfg["agent"]["task"]["max_nudges"]), ("cuda", True, 5))
        self.assertEqual(self.read(), {"agent": {"max_turns": 9}, "config_version": config_store.CONFIG_VERSION})

    def test_older_user_files_are_migrated_once(self):
        self.path.write_text(json.dumps({"config_version": 1, "old": {"key": 1}}), encoding="utf-8")

        def v1_to_v2(raw):
            raw["new"] = raw.pop("old")
            return raw
        with mock.patch.object(config_store, "CONFIG_VERSION", 2), mock.patch.dict(config_store.MIGRATIONS, {1: v1_to_v2}):
            config_store.migrate_user_config(self.path)
            self.assertEqual(self.read(), {"config_version": 2, "new": {"key": 1}})
            config_store.migrate_user_config(self.path)                     # already current: untouched
            self.assertEqual(self.read(), {"config_version": 2, "new": {"key": 1}})
        config_store.migrate_user_config(self.dir / "missing.json")         # no file: nothing to migrate
        self.assertFalse((self.dir / "missing.json").exists())

    def test_version_1_files_move_to_the_provider_settings(self):
        v1 = {"config_version": 1,
              "llm": {"claude": {"model": "claude-opus-5-5", "effort": "high", "timeout_seconds": 45,
                                 "model_options": ["claude-opus-5-5"]}},
              "greeting": {"model": "claude-haiku-4-5"},
              "claude_code": {"enabled": True, "default_project": "shop", "require_confirmation": False,
                              "timeout_seconds": 600, "confirm_affirmative": ["ja"], "confirmation_window_ms": 3000},
              "tools": {"permissions": {"delegate_to_claude": "allow", "open_app": "ask"}}}
        self.path.write_text(json.dumps(v1), encoding="utf-8")
        config_store.migrate_user_config(self.path)
        all_on = {"workspace_read": True, "workspace_write": True, "terminals": True, "desktop": True,
                  "system": True, "email": True, "coding_agent": True}
        self.assertEqual(self.read(), {
            "config_version": config_store.CONFIG_VERSION,
            "setup": {"complete": True},                                # set up before the wizard existed
            "llm": {"providers": {"claude_code": {"model": "claude-opus-5-5", "effort": "high", "timeout_seconds": 45,
                                                  "utility_model": "claude-haiku-4-5"}}},
            "coding_agent": {"default_project": "shop", "require_confirmation": False, "timeout_seconds": 600},
            "voice_approval": {"affirmative": ["ja"], "window_ms": 3000},
            "tools": {"permissions": {"delegate_to_coding_agent": "allow", "open_app": "ask"},
                      "groups": all_on}})                               # it could do everything: still can
        cfg = config_store.load_config(self.path)
        self.assertEqual(cfg["llm"]["main"]["provider"], "claude_code")
        self.assertEqual(cfg["llm"]["providers"]["claude_code"]["model"], "claude-opus-5-5")
        self.assertEqual(cfg["coding_agent"]["cli"], "claude")

    def test_version_3_files_get_tool_groups_that_keep_what_they_could_do(self):
        from src.tools.groups import enabled_groups
        old_deny = {t: "deny" for t in ("desktop_click", "run_command", "send_email")}
        cases = [
            ({"preset": "everyday", "permissions": dict(old_deny, open_app="ask")},
             {"basic", "workspace_read", "system"}, {"open_app": "ask"}),
            ({"preset": "work", "permissions": dict(old_deny)},
             {"basic", "workspace_read", "system", "workspace_write", "terminals", "email", "coding_agent"},
             {"run_command": "deny", "send_email": "deny"}),        # not the wizard's: the user's own denies
            ({"permissions": {"run_command": "deny"}}, None, {"run_command": "deny"}),
        ]
        for tools, groups, perms in cases:
            self.path.write_text(json.dumps({"config_version": 3, "tools": tools}), encoding="utf-8")
            config_store.migrate_user_config(self.path)
            cfg = config_store.load_config(self.path)
            from src.tools.groups import GROUPS
            self.assertEqual(enabled_groups(cfg), groups or set(GROUPS), tools)
            self.assertEqual(cfg["tools"]["permissions"], perms, tools)
        self.path.write_text(json.dumps({"config_version": 3, "tools": {"groups": {"email": True}}}), encoding="utf-8")
        config_store.migrate_user_config(self.path)
        self.assertEqual(self.read()["tools"], {"groups": {"email": True}}, "already has groups: untouched")

    def test_version_4_files_lose_the_phone_settings(self):
        self.path.write_text(json.dumps({"config_version": 4, "phone": {"enabled": True, "port": 9000},
                                         "agent": {"max_turns": 9}}), encoding="utf-8")
        config_store.migrate_user_config(self.path)
        self.assertEqual(self.read(), {"agent": {"max_turns": 9}, "config_version": config_store.CONFIG_VERSION})

    def test_new_installs_keep_the_risky_tool_groups_off(self):
        from src.tools.groups import GROUPS, enabled_groups
        cfg = config_store.load_config(self.dir / "missing.json")
        self.assertEqual(enabled_groups(cfg), {"basic", "workspace_read", "system"})
        shipped = config_store.defaults()["tools"]["groups"]
        self.assertEqual(shipped, {g: v["default"] for g, v in GROUPS.items() if not v.get("locked")},
                         "defaults.json and groups.py agree")
        self.assertTrue(cfg["tools"]["untrusted_asks"])
        self.assertFalse(cfg["voice_approval"]["high_risk"])
        self.assertEqual(config_store.validate_update({"tools.groups": {"basic": False}})[:1],
                         ["tools.groups.basic: not a group that can be switched"])
        self.assertTrue(config_store.validate_update({"tools.groups": {"desktop": "yes"}}))
        self.assertTrue(config_store.validate_update({"tools.extensions": ["not_there"]}))
        self.assertEqual(config_store.validate_update({"tools.groups": {"desktop": True}, "tools.extensions": []}), [])

    def test_a_new_install_runs_setup_and_an_existing_one_does_not(self):
        from src.app import setup
        self.assertTrue(setup.needed(config_store.load_config(self.path)), "no settings yet: the wizard shows")
        self.path.write_text(json.dumps({"config_version": 2, "agent": {"max_turns": 9}}), encoding="utf-8")
        config_store.migrate_user_config(self.path)
        self.assertFalse(setup.needed(config_store.load_config(self.path)))

    def test_version_1_delegation_off_or_custom_cli(self):
        self.path.write_text(json.dumps({"config_version": 1, "claude_code": {"enabled": False}}), encoding="utf-8")
        config_store.migrate_user_config(self.path)
        self.assertEqual(self.read()["coding_agent"], {"cli": "off"})
        self.path.write_text(json.dumps({"config_version": 1, "claude_code": {"cli_path": "D:/tools/claude.exe"}}),
                             encoding="utf-8")
        config_store.migrate_user_config(self.path)
        self.assertEqual(self.read()["coding_agent"], {"cli": "custom", "custom_command": ["D:/tools/claude.exe", "-p"]})

    def test_provider_settings_are_validated(self):
        cfg = config_store.update_config({"llm.main.provider": "ollama", "llm.providers.ollama.model": "qwen3:8b",
                                          "llm.daily_budget_usd": 2, "llm.providers.openai.vision": None}, self.path)
        self.assertEqual((cfg["llm"]["main"]["provider"], cfg["llm"]["daily_budget_usd"]), ("ollama", 2.0))
        config_store.update_config({"llm.daily_budget_usd": None}, self.path)        # back to no limit
        for bad in ({"llm.main.provider": "skynet"}, {"llm.utility.provider": "nope"},
                    {"llm.providers.claude_code.model": " "}, {"llm.providers.anthropic.effort": "turbo"},
                    {"tools.profile": "everything"}, {"coding_agent.cli": "vim"}, {"llm.claude.model": "x"}):
            with self.assertRaises(ValueError, msg=bad):
                config_store.update_config(bad, self.path)
        config_store.update_config({"llm.utility.provider": "", "llm.providers.openai.model": ""}, self.path)

    def test_wake_word_settings_are_gone(self):
        for key in ("wakeword.enabled", "wakeword.threshold", "pipeline.wake_ack", "gui.show_on_wake"):
            with self.assertRaises(ValueError, msg=key):
                config_store.update_config({key: True}, self.path)
        self.assertNotIn("wakeword", config_store.defaults())

    def test_shipped_defaults_are_generic_and_complete(self):
        d = config_store.defaults()
        for key in config_store.EDITABLE:                                    # every Settings control has a default
            self.assertIsNot(config_store.get_path(d, key, _ABSENT), _ABSENT, key)
        e = d["email"]
        self.assertEqual((e["username"], e["from_address"], e["from_name"]), ("", "", ""))
        self.assertEqual(d["tts"]["chatterbox"]["python"], "")
        self.assertEqual(d["coding_agent"]["default_project"], "")
        self.assertIsNone(d["audio"]["input_device_index"])                  # the system default microphone
        self.assertFalse(d["agent"]["full_access"])
        self.assertNotIn("phone", d)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_the_users_profile_goes_on_top_of_the_shipped_one(self):
        path = self.dir / "user_profile.json"
        path.write_text(json.dumps({"apps": {"notepad": {"path": "notepad++.exe"}, "slack": {"path": "slack.exe"}},
                                    "user": {"name": "Sam"}}), encoding="utf-8")
        prof = config_store.load_profile(path)
        self.assertEqual(prof["apps"]["notepad"]["path"], "notepad++.exe")    # the user's entry replaces the default
        self.assertIn("slack", prof["apps"])                                  # and adds to the common apps
        self.assertIn("calculator", prof["apps"])
        self.assertEqual(prof["user"], {"name": "Sam"})
        self.assertIn("play", prof["media"])

    def test_without_a_profile_the_common_apps_media_keys_and_system_commands_remain(self):
        prof = config_store.load_profile(self.dir / "none.json")
        self.assertIn("notepad", prof["apps"])
        self.assertIn("volume_up", prof["media"])
        self.assertTrue(prof["system"]["shutdown"]["confirm"])
        self.assertTrue(prof["system"]["sleep"]["confirm"])
        self.assertNotIn("user", prof)
        self.assertNotIn("projects", prof)

    def test_default_apps_are_offered_only_when_installed(self):
        shipped = self.dir / "profile_defaults.json"
        exe = Path(sys.executable)
        shipped.write_text(json.dumps({"apps": {
            "python": {"path": str(exe)},
            "python_via_var": {"path": "%JARVIS_TEST_DIR%/" + exe.name},
            "missing": {"path": str(self.dir / "nope.exe")},
            "on_path": {"path": "notepad.exe"}}}), encoding="utf-8")
        with mock.patch.object(config_store, "PROFILE_DEFAULTS_PATH", shipped), \
                mock.patch.dict(os.environ, {"JARVIS_TEST_DIR": str(exe.parent)}):
            apps = config_store.load_profile(self.dir / "none.json")["apps"]
        self.assertEqual(sorted(apps), ["on_path", "python", "python_via_var"])
        self.assertEqual(Path(apps["python_via_var"]["path"]), exe)          # %VAR% expanded


if __name__ == "__main__":
    unittest.main()
