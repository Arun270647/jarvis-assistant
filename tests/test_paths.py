"""Where Jarvis keeps its files (src/core/paths.py), the platform layer, and a fresh install: no settings
file and no profile must still give a working router, tool registry, prompt and workspace policy."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core import paths
from src.platform import current
from src.platform.base import Platform


class PathTests(unittest.TestCase):
    def test_jarvis_home_holds_everything(self):
        home = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"JARVIS_HOME": str(home)}):
            self.assertEqual(paths.home(), home)
            self.assertEqual(paths.config_file(), home / "config.json")
            self.assertEqual(paths.profile_file(), home / "user_profile.json")
            self.assertEqual(paths.db_file(), home / "jarvis.db")
            self.assertEqual(paths.log_dir(), home / "logs")
            self.assertEqual(paths.models_dir(), home / "models")
            self.assertEqual(paths.cache_dir(), home / "cache")
            self.assertEqual(paths.data_dirs(), [home, home / "models", home / "cache"])

    def test_relative_jarvis_home_is_inside_the_app_folder(self):
        with mock.patch.dict(os.environ, {"JARVIS_HOME": "userdata"}):
            self.assertEqual(paths.home(), paths.APP_ROOT / "userdata")

    def test_without_jarvis_home_the_platform_folders_are_used(self):
        env = {k: v for k, v in os.environ.items() if k != "JARVIS_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            plat = current()
            self.assertEqual(paths.home(), plat.data_dir())
            self.assertEqual(paths.models_dir(), plat.models_dir())
            self.assertEqual(paths.cache_dir(), plat.cache_dir())
            if sys.platform == "win32":
                self.assertEqual(paths.home(), Path(os.environ["APPDATA"]) / "Jarvis")
                local = Path(os.environ["LOCALAPPDATA"]) / "Jarvis"
                self.assertEqual((paths.models_dir(), paths.cache_dir()), (local / "models", local / "cache"))

    def test_model_paths(self):
        models = paths.models_dir()
        self.assertEqual(paths.model_path("piper"), models / "piper")
        self.assertEqual(paths.model_path("en.onnx", "piper"), models / "piper" / "en.onnx")
        absolute = Path(tempfile.mkdtemp())
        self.assertEqual(paths.model_path(str(absolute)), absolute)

    def test_whisper_names_download_and_folders_load_locally(self):
        root = str(paths.models_dir() / "whisper")
        self.assertEqual(paths.whisper_model("small"), ("small", root))
        self.assertEqual(paths.whisper_model("large-v3-turbo"), ("large-v3-turbo", root))
        local = paths.models_dir() / "whisper" / "my-model"
        local.mkdir(parents=True, exist_ok=True)
        self.assertEqual(paths.whisper_model("my-model"), (str(local), root))       # an existing folder wins
        folder = Path(tempfile.mkdtemp())
        self.assertEqual(paths.whisper_model(str(folder)), (str(folder), root))


class PlatformTests(unittest.TestCase):
    def test_current_platform(self):
        plat = current()
        self.assertIs(plat, current())                                     # one instance
        self.assertEqual(plat.name, "windows" if sys.platform == "win32" else "generic")

    def test_generic_platform_has_folders_but_no_os_services(self):
        plat = Platform()
        self.assertEqual(plat.data_dir().name, "Jarvis")
        with self.assertRaises(NotImplementedError):
            plat.write_secret("JARVIS/TEST/x", "x", "y")
        with self.assertRaises(NotImplementedError):
            plat.set_autostart(True, "jarvis.exe")
        self.assertFalse(plat.autostart_enabled())

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_startup_shortcut_location(self):
        plat = current()
        self.assertEqual(plat.startup_shortcut().name, "Jarvis.lnk")
        self.assertEqual(plat.startup_shortcut().parent.name, "Startup")


class FreshInstallTests(unittest.TestCase):
    """A new user: no config.json and no user_profile.json in the data folder."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="jarvis-fresh-"))
        self.env = mock.patch.dict(os.environ, {"JARVIS_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_router_registry_prompt_and_workspace_work(self):
        from src.agent.prompt import build_agent_system_prompt, describe_user
        from src.core import config_store
        from src.core.workspace import WorkspaceError, WorkspacePolicy
        from src.router.action_router import ActionRouter
        from src.tools.registry import ToolContext, build_default_registry
        from src.tools.policy import PolicyExecutor

        cfg = config_store.load_config()
        self.assertEqual(cfg, config_store.defaults())
        router = ActionRouter()
        router.dry_run = True
        self.assertIn("notepad", router.profile["apps"])
        reg = build_default_registry(router, None, cfg)
        ex = PolicyExecutor(reg, lambda: cfg, None, None)
        self.assertEqual(ex.execute("open_app", {"app_name": "notepad"}, ToolContext()).status, "dry_run")
        self.assertEqual(ex.execute("media_control", {"control": "volume_up"}, ToolContext()).status, "dry_run")

        self.assertEqual(describe_user(router.profile), "")
        self.assertNotIn("About the user", build_agent_system_prompt(reg.names(), router.profile))

        ws = WorkspacePolicy(router.profile, cfg)
        for attempt in (lambda: ws.resolve("notes.txt"), lambda: ws.resolve(str(self.home.parent / "x.txt"))):
            with self.assertRaises(WorkspaceError) as caught:
                attempt()
            self.assertIn("No project folders are set up yet", str(caught.exception))
            self.assertIn(str(paths.profile_file()), str(caught.exception))

        delegation = router.resolve_delegation({"task": "fix the tests"})
        self.assertFalse(delegation["success"])
        self.assertFalse(any(self.home.iterdir()), "nothing is written just by starting up")

    def test_pipeline_validation_only_needs_claude_code(self):
        import src.pipeline as pipeline
        from src.core import config_store
        cfg = config_store.load_config()
        with mock.patch("shutil.which", lambda name: "C:/fake/claude.exe"):
            self.assertEqual(pipeline.validate_config(cfg), [])     # no mic index, models or profile needed
        with mock.patch("shutil.which", lambda name: None):
            self.assertTrue(any("Claude Code CLI not found" in e for e in pipeline.validate_config(cfg)))


if __name__ == "__main__":
    unittest.main()
