"""Common apps found in the Start Menu (src/core/app_discovery.py), and opening a shortcut like Explorer does."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core import app_discovery, config_store, winproc


class AppDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Spotify").mkdir()
        for rel in ("Spotify/Spotify.lnk", "Spotify/Uninstall Spotify.lnk", "Visual Studio Code.lnk",
                    "Excel.lnk", "Some Random Tool.lnk", "Zoom Workplace.lnk"):
            (self.root / rel).write_bytes(b"")
        app_discovery._cache.clear()

    def test_only_common_apps_with_aliases(self):
        apps = app_discovery.discover([self.root], kind="lnk")
        self.assertEqual(sorted(apps), ["excel", "spotify", "vs code", "zoom"])
        self.assertEqual(Path(apps["spotify"]["path"]).name, "Spotify.lnk", "the uninstaller is skipped")
        self.assertIn("vscode", apps["vs code"]["aliases"])
        self.assertEqual(apps["zoom"]["source"], "start_menu")

    def test_cache_follows_changes(self):
        self.assertNotIn("slack", app_discovery.discover([self.root], kind="lnk"))
        later = os.stat(self.root).st_mtime_ns + 2_000_000_000         # a real install is never in the same clock tick
        (self.root / "Slack.lnk").write_bytes(b"")
        os.utime(self.root, ns=(later, later))
        self.assertIn("slack", app_discovery.discover([self.root], kind="lnk"))

    def test_profile_includes_discovered_apps_but_user_entries_win(self):
        found = {"spotify": {"path": "C:/x/Spotify.lnk", "aliases": ["music"], "source": "start_menu"},
                 "calculator": {"path": "C:/x/Other.lnk", "aliases": [], "source": "start_menu"}}
        with mock.patch.dict(os.environ, {"JARVIS__apps__discover": "true"}), \
                mock.patch.object(app_discovery, "discover", return_value=found):
            apps = config_store.profile_defaults()["apps"]
        self.assertEqual(apps["spotify"]["path"], "C:/x/Spotify.lnk")
        self.assertNotEqual(apps["calculator"]["path"], "C:/x/Other.lnk", "the shipped entry is kept")
        self.assertNotIn("spotify", config_store.profile_defaults()["apps"], "tests see no Start Menu (tests/__init__)")
        with mock.patch.dict(os.environ, {"JARVIS__apps__discover": "false"}), \
                mock.patch.object(app_discovery, "discover", return_value=found):
            self.assertNotIn("spotify", config_store.profile_defaults()["apps"], "apps.discover = false turns it off")

    @unittest.skipUnless(winproc.IS_WINDOWS, "Windows shortcuts")
    def test_a_shortcut_opens_like_a_double_click(self):
        lnk = self.root / "Excel.lnk"
        with mock.patch("os.startfile") as startfile:
            self.assertIsNone(winproc.launch_program([str(lnk)]))
        startfile.assert_called_once_with(str(lnk))


if __name__ == "__main__":
    unittest.main()
