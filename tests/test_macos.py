"""The macOS parts that can be checked on any OS: the Keychain and LaunchAgent through fakes, the desktop key parser,
the zsh rules for typed lines, what runs in a Terminal tab, Applications-folder discovery, the Jarvis.app layout,
the release's disk image (updates and Install for me), the updater script, and the Mac build's helpers. The parts that need a real Mac run in CI on macos-latest (the
system voice, permissions) or in the maintainer's testing."""
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.workspace import WorkspacePolicy
from src.platform.macos import BUNDLE_ID, KEYCHAIN_ACCOUNT, MacPlatform
from src.tools import desktop_mac, terminal_mac


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, account):
        return self.store.get((service, account))

    def set_password(self, service, account, secret):
        self.store[(service, account)] = secret

    def delete_password(self, service, account):
        del self.store[(service, account)]


class MacPlatformTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.mac = MacPlatform()

    def test_secrets_live_in_the_keychain_under_the_target(self):
        ring = FakeKeyring()
        with mock.patch.dict(sys.modules, {"keyring": ring}):
            self.mac.write_secret("JARVIS/anthropic", "ignored", "sk-test")
            self.assertEqual(ring.store, {("JARVIS/anthropic", KEYCHAIN_ACCOUNT): "sk-test"})
            self.assertEqual(self.mac.read_secret("JARVIS/anthropic"), "sk-test")
            self.mac.delete_secret("JARVIS/anthropic")
            self.mac.delete_secret("JARVIS/anthropic")                 # already gone: no error
            self.assertIsNone(self.mac.read_secret("JARVIS/anthropic"))

    def test_start_at_login_is_a_launch_agent(self):
        with mock.patch("pathlib.Path.home", return_value=self.home):
            self.assertFalse(self.mac.autostart_enabled())
            self.assertTrue(self.mac.set_autostart(True, "/usr/bin/open", '-a "/Applications/Jarvis.app" --args --minimized',
                                                   "/Applications"))
            job = plistlib.loads((self.home / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist").read_bytes())
            self.assertEqual(job["ProgramArguments"],
                             ["/usr/bin/open", "-a", "/Applications/Jarvis.app", "--args", "--minimized"])
            self.assertTrue(job["RunAtLoad"])
            self.assertEqual(job["Label"], BUNDLE_ID)
            self.assertFalse(self.mac.set_autostart(False, "/usr/bin/open"))
            self.assertFalse(self.mac.autostart_enabled())

    def test_privacy_pages(self):
        with mock.patch.object(self.mac, "open_path") as opened:
            self.assertTrue(self.mac.privacy_settings("accessibility"))
            self.assertFalse(self.mac.privacy_settings("camera"))
        opened.assert_called_once_with("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")

    def test_volume_keys_use_applescript(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("src.core.winproc.run_hidden", return_value=done) as run:
            self.mac.send_media_key("volume_mute")
        self.assertEqual(run.call_args[0][0][:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("output muted", run.call_args[0][0][2])
        with self.assertRaises(ValueError):
            self.mac.send_media_key("eject")

    @unittest.skipIf(sys.platform == "darwin", "a real Mac answers")
    def test_permissions_are_unknown_without_pyobjc(self):
        self.assertEqual(self.mac.permission("microphone"), "unknown")


class MacKeyTests(unittest.TestCase):
    def test_parse_keys(self):
        self.assertEqual(desktop_mac.parse_keys("cmd+shift+t"), [55, 56, 17])
        self.assertEqual(desktop_mac.parse_keys("Enter"), [36])
        self.assertEqual(desktop_mac.parse_keys("option+left"), [58, 123])
        self.assertEqual(desktop_mac.SELECT_ALL, [55, 0])

    def test_launcher_and_session_shortcuts_are_refused(self):
        for bad in ("cmd+space", "ctrl+space", "cmd+option+esc", "cmd+shift+q", "ctrl+cmd+q", "fn+f", "banana", ""):
            with self.assertRaises(ValueError, msg=bad):
                desktop_mac.parse_keys(bad)


class ZshLineTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.proj = self.dir / "proj"
        (self.proj / "sub").mkdir(parents=True)
        self.profile = {"projects": {"p": {"path": str(self.proj), "commands": ["npm test"],
                                           "deny_commands": ["npm publish"]}}}

    def verdict(self, line, full=False):
        ws = WorkspacePolicy(self.profile, {"agent": {"full_access": full}})
        return terminal_mac.classify_line(ws, line, self.proj)[0]

    def test_commands_follow_run_command_rules(self):
        for line, want in [("npm test", "allow"), ("git status", "allow"), ("ls -la", "allow"), ("cd sub", "allow"),
                           ("python3 evil.py", "ask"), ("npm test $HOME", "ask"), ("npm publish", "deny"),
                           ("sudo ls", "deny"), ("zsh -c x", "deny"), ("bash -c x", "deny"), ("osascript -e x", "deny"),
                           ("git push --force", "deny"), ("rm -rf sub", "deny")]:
            self.assertEqual(self.verdict(line), want, line)

    def test_zsh_specific_rules(self):
        outside = self.dir.parent.as_posix()                           # zsh rules: forward slashes, also on Windows
        for line, want in [("npm test; rm x", "deny"), ("echo hi > f.txt", "deny"), ("ls | less", "deny"),
                           ("echo $(whoami)", "deny"), ("echo `whoami`", "deny"), ("npm test && ls", "deny"),
                           ("cat .env", "deny"), (f"cd {outside}", "deny"), ("cd -", "deny"), ("cd", "deny"),
                           ("open -a Terminal", "ask"), ("export PATH=x", "ask"), ("source x.sh", "ask"),
                           (f"cp a.txt {outside}", "deny"), ("mkdir build", "ask"),
                           ('echo "unbalanced', "deny")]:
            self.assertEqual(self.verdict(line), want, line)

    def test_full_access(self):
        self.assertEqual(self.verdict("python3 build.py", full=True), "allow")
        self.assertEqual(self.verdict(f"cd {self.dir.parent.as_posix()}", full=True), "allow")
        self.assertEqual(self.verdict("sudo ls", full=True), "deny")


class TerminalTabTests(unittest.TestCase):
    ROWS = [{"pid": 100, "pgid": 100, "tpgid": 200, "name": "login"},
            {"pid": 101, "pgid": 101, "tpgid": 200, "name": "zsh"},
            {"pid": 200, "pgid": 200, "tpgid": 200, "name": "node"},
            {"pid": 201, "pgid": 200, "tpgid": 200, "name": "esbuild"}]

    def test_state_at_the_prompt_and_with_a_program(self):
        prompt = [dict(r, tpgid=101) for r in self.ROWS[:2]]
        self.assertEqual(terminal_mac.state(prompt), {"shell": 101, "programs": []})
        self.assertEqual(terminal_mac.state(self.ROWS), {"shell": 101, "programs": ["node", "esbuild"]})
        self.assertEqual(terminal_mac.state([]), {"shell": None, "programs": []})

    def test_read_reports_the_shells_folder(self):
        prompt = [dict(r, tpgid=101) for r in self.ROWS[:2]]
        with mock.patch.object(terminal_mac, "_script", return_value="Last login\nuser@mac proj % \n\n") as script, \
                mock.patch.object(terminal_mac, "tty_processes", return_value=prompt), \
                mock.patch.object(terminal_mac, "folder_of", return_value="/Users/alex/proj") as folder:
            s = terminal_mac.request({"op": "read", "tty": "/dev/ttys003", "lines": 10})
        self.assertEqual(script.call_args[0][1], "/dev/ttys003")
        folder.assert_called_once_with(101)
        self.assertEqual(s["cwd"], "/Users/alex/proj")
        self.assertEqual(s["programs"], [])
        self.assertEqual(s["lines"], ["Last login", "user@mac proj %"])

    def test_ctrl_c_interrupts_the_foreground_group_without_keys(self):
        with mock.patch.object(terminal_mac, "tty_processes", return_value=self.ROWS), \
                mock.patch.object(terminal_mac.os, "killpg", create=True) as killpg:
            terminal_mac.request({"op": "key", "tty": "/dev/ttys003", "key": "ctrl+c"})
        killpg.assert_called_once_with(200, terminal_mac.signal.SIGINT)

    def test_typing_a_line_needs_no_focus(self):
        with mock.patch.object(terminal_mac, "_script") as script, \
                mock.patch.object(terminal_mac, "_needs_accessibility") as needs:
            terminal_mac.request({"op": "type", "tty": "/dev/ttys003", "text": "npm test", "submit": True})
        self.assertEqual(script.call_args[0][1:], ("/dev/ttys003", "npm test"))
        needs.assert_not_called()

    def test_automation_refusal_is_explained(self):
        refused = subprocess.CompletedProcess([], 1, "", "execution error: Not authorized to send Apple events to "
                                                         "Terminal. (-1743)")
        with mock.patch("src.core.winproc.run_hidden", return_value=refused):
            with self.assertRaises(OSError) as e:
                terminal_mac.request({"op": "read", "tty": "/dev/ttys003"})
        self.assertIn("Automation", str(e.exception))


class ApplicationsDiscoveryTests(unittest.TestCase):
    def test_apps_and_utilities_but_not_inside_bundles(self):
        from src.core import app_discovery
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for rel in ("Google Chrome.app/Contents/MacOS", "Utilities/Terminal.app", "Spotify.app",
                    "Visual Studio Code.app/Contents/Frameworks/Code Helper.app", "zoom.us.app"):
            (root / rel).mkdir(parents=True)
        app_discovery._cache.clear()
        apps = app_discovery.discover([root], kind="app")
        self.assertEqual(sorted(apps), ["chrome", "spotify", "vs code", "zoom"])
        self.assertEqual(Path(apps["chrome"]["path"]).name, "Google Chrome.app")
        self.assertEqual(apps["chrome"]["source"], "applications")


class BundleLayoutTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = self.dir / "Jarvis.app"
        for rel in ("Contents/MacOS", "Contents/Resources/app", "Contents/Resources/runtime/bin"):
            (self.app / rel).mkdir(parents=True)
        (self.app / "Contents" / "MacOS" / "Jarvis").write_bytes(b"")

    def test_install_root_is_the_app(self):
        from src.core import paths
        with mock.patch.object(paths, "APP_ROOT", self.app / "Contents" / "Resources" / "app"):
            self.assertEqual(paths.install_root(), self.app)

    def test_is_jarvis_app(self):
        from src.app.install import is_jarvis_app
        self.assertTrue(is_jarvis_app(self.app))
        self.assertFalse(is_jarvis_app(self.dir))
        other = self.dir / "Other.app"
        other.mkdir()
        self.assertFalse(is_jarvis_app(other))

    def test_start_at_login_opens_the_app(self):
        from src.core import startup
        with mock.patch.object(startup.sys, "platform", "darwin"):
            command = startup.launch_command(self.app)
        self.assertEqual(command["program"], "/usr/bin/open")
        self.assertEqual(command["arguments"], f'-a "{self.app}" --args')


class MacDiskImageTests(unittest.TestCase):
    """The release's .dmg: the updater copies Jarvis.app out of it, and Install for me ejects it (hdiutil faked)."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.commands, self.ditto_fails = [], False

    def fake_run(self, command, timeout=None, **kw):
        from src.app import updates
        self.commands.append([Path(command[0]).name] + [str(c) for c in command[1:]])
        if command[1:2] == ["attach"]:
            app = Path(command[command.index("-mountpoint") + 1]) / "Jarvis.app"
            for rel in updates.MAC_REQUIRED:
                (app / rel).parent.mkdir(parents=True, exist_ok=True)
                (app / rel).write_text("x", encoding="utf-8")
        elif command[1:2] == ["detach"]:
            shutil.rmtree(Path(command[2]) / "Jarvis.app", ignore_errors=True)    # leaves the empty mount point
        elif Path(command[0]).name == "ditto":
            if self.ditto_fails:
                raise subprocess.CalledProcessError(1, command)
            if Path(command[1]).exists():
                shutil.copytree(command[1], command[2])
            else:
                Path(command[2]).mkdir(parents=True)                         # Install for me: /Volumes isn't here
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    def test_update_copies_the_app_out_of_the_image_and_detaches_it(self):
        from src.app import updates
        with mock.patch.object(updates, "IS_MAC", True), mock.patch("src.core.winproc.run_hidden", self.fake_run):
            new = updates.stage(self.dir / "Jarvis-0.2.0-macos-arm64.dmg", Path("/Applications/Jarvis.app"))
        staging = updates.paths.cache_dir() / "update"                         # outside the app: its signature stays
        self.addCleanup(shutil.rmtree, staging, True)
        self.assertEqual(new, staging / "Jarvis.app")
        self.assertTrue(all((new / rel).is_file() for rel in updates.MAC_REQUIRED))
        self.assertEqual([c[:2] for c in self.commands], [["hdiutil", "attach"], ["ditto", self.commands[1][1]],
                                                          ["hdiutil", "detach"]])
        self.assertIn("-readonly", self.commands[0])
        self.assertIn("-nobrowse", self.commands[0])
        self.assertFalse(Path(self.commands[2][2]).exists())                    # the mount point is cleaned up

    def test_the_image_is_detached_when_copying_fails(self):
        from src.app import updates
        self.ditto_fails = True
        with mock.patch.object(updates, "IS_MAC", True), mock.patch("src.core.winproc.run_hidden", self.fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                updates.stage(self.dir / "Jarvis.dmg", Path("/Applications/Jarvis.app"))
        self.assertEqual(self.commands[-1][:2], ["hdiutil", "detach"])

    def test_disk_image_from_hdiutil_info(self):
        from src.app import install
        dmg = "/Users/alex/Downloads/Jarvis-0.2.0-macos-arm64.dmg"
        info = plistlib.dumps({"images": [{"image-path": dmg, "system-entities": [
            {"dev-entry": "/dev/disk4"}, {"dev-entry": "/dev/disk4s1", "mount-point": "/Volumes/Jarvis"}]}]})
        with mock.patch("src.core.winproc.run_hidden", return_value=subprocess.CompletedProcess([], 0, stdout=info)):
            self.assertEqual(install.disk_image(Path("/Volumes/Jarvis/Jarvis.app")),
                             (Path("/Volumes/Jarvis"), Path(dmg)))
            self.assertIsNone(install.disk_image(Path("/Volumes/Other/Jarvis.app")))
        self.assertIsNone(install.disk_image(Path("/Applications/Jarvis.app")))

    def test_install_from_the_image_ejects_it_and_deletes_the_dmg(self):
        from src.app import install
        mount, dmg = Path("/Volumes/Jarvis"), self.dir / "Downloads" / "Jarvis-0.2.0-macos-arm64.dmg"
        scripts = []
        def popen(command, **kw):
            scripts.append(Path(command[1]).read_text(encoding="utf-8"))
            Path(command[1]).unlink()
        with mock.patch("src.core.paths.install_root", return_value=mount / "Jarvis.app"),                 mock.patch.object(install, "target", return_value=self.dir / "Applications" / "Jarvis.app"),                 mock.patch.object(install, "disk_image", return_value=(mount, dmg)),                 mock.patch("src.core.startup.is_startup_enabled", return_value=False),                 mock.patch("src.core.winproc.run_hidden", self.fake_run),                 mock.patch("src.core.winproc.popen_hidden", popen):
            result = install._install_mac(remove_copy=True, pid=4242)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removing"], str(dmg))
        self.assertIn(f'/usr/bin/hdiutil detach "{mount}" -quiet', scripts[0])
        self.assertIn(f'rm -f "{dmg}"', scripts[0])
        self.assertNotIn("rm -rf", scripts[0])


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class MacUpdaterScriptTests(unittest.TestCase):
    """src/app/updates.py's MAC_UPDATER, run with sh against folders standing in for Jarvis.app."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app, self.new = self.dir / "Applications" / "Jarvis.app", self.dir / "update" / "Jarvis.app"
        self.backup, self.result = self.dir / "update-old" / "Jarvis.app", self.dir / "update-result.txt"
        for folder, version in ((self.app, "old"), (self.new, "new")):
            folder.mkdir(parents=True)
            (folder / "version").write_text(version, encoding="utf-8")

    def run_updater(self):
        from src.app.updates import MAC_UPDATER
        script = self.dir / "updater.sh"
        script.write_text(MAC_UPDATER, encoding="utf-8", newline="\n")
        posix = lambda p: p.as_posix()                                  # Git's sh on Windows takes forward slashes
        subprocess.run([shutil.which("sh"), posix(script), "999999", posix(self.app), posix(self.new),
                        posix(self.backup), posix(self.result), "true"], check=True, timeout=60)
        return self.result.read_text(encoding="utf-8").strip()

    def test_swaps_and_keeps_the_old_app(self):
        self.assertEqual(self.run_updater(), "updated")
        self.assertEqual((self.app / "version").read_text(encoding="utf-8"), "new")
        self.assertEqual((self.backup / "version").read_text(encoding="utf-8"), "old")

    def test_puts_the_old_app_back_when_the_new_one_is_missing(self):
        shutil.rmtree(self.new)
        self.assertTrue(self.run_updater().startswith("failed:"))
        self.assertEqual((self.app / "version").read_text(encoding="utf-8"), "old")


class MacBuildTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
        self.addCleanup(sys.path.remove, sys.path[0])
        import build_macos
        self.b = build_macos

    def test_info_plist(self):
        info = self.b.info_plist("0.2.0-beta.1")
        self.assertEqual(info["CFBundleShortVersionString"], "0.2.0")
        self.assertEqual(info["CFBundleVersion"], "0.2.0-beta.1")
        self.assertEqual(info["CFBundleIdentifier"], BUNDLE_ID)
        self.assertEqual(info["CFBundleExecutable"], "Jarvis")
        self.assertEqual(info["LSMinimumSystemVersion"], "14.0", "numpy and onnxruntime ship macOS 14 wheels")
        self.assertIn("Nothing is recorded", info["NSMicrophoneUsageDescription"])
        self.assertTrue(info["NSAppleEventsUsageDescription"])
        plistlib.dumps(info)                                            # valid plist types

    def test_latest_json_gets_the_mac_entry(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        latest = folder / "latest.json"
        latest.write_text(json.dumps({"version": "0.2.0", "notes": "n", "windows": {"url": "w"}}), encoding="utf-8")
        merged = self.b.merge_latest(latest, "0.2.0", "", {"url": "m", "sha256": "x", "size": 1})
        self.assertEqual(merged["windows"], {"url": "w"})
        self.assertEqual(merged["macos"]["url"], "m")
        self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["notes"], "n")
        with self.assertRaises(SystemExit):
            self.b.merge_latest(latest, "0.3.0", "", {})

    def test_dmg_name(self):
        self.assertEqual(self.b.dmg_name("0.2.0-beta.1"), "Jarvis-0.2.0-beta.1-macos-arm64.dmg")

    def test_runtime_is_pinned(self):
        pins = json.loads((Path(__file__).resolve().parents[1] / "packaging" / "runtime.json").read_text(encoding="utf-8"))
        mac = pins["macos_python"]
        self.assertEqual(mac["arch"], "arm64")
        self.assertEqual(len(mac["sha256"]), 64)
        self.assertEqual(mac["version"].rsplit(".", 1)[0], pins["python"]["version"].rsplit(".", 1)[0])


class MacProfileTests(unittest.TestCase):
    def test_media_keys_and_system_commands_like_windows(self):
        config = Path(__file__).resolve().parents[1] / "config"
        mac = json.loads((config / "profile_defaults.macos.json").read_text(encoding="utf-8"))
        win = json.loads((config / "profile_defaults.json").read_text(encoding="utf-8"))
        self.assertLessEqual(set(mac["media"]), set(win["media"]))         # no stop key on a Mac
        self.assertLessEqual({"play", "pause", "next", "previous", "volume_up", "volume_down", "mute"}, set(mac["media"]))
        self.assertLessEqual({"lock", "sleep", "shutdown", "restart"}, set(mac["system"]))
        for key in ("sleep", "shutdown", "restart"):
            self.assertTrue(mac["system"][key].get("confirm"), key)
        for app in mac["apps"].values():
            self.assertTrue(app["path"].endswith(".app"), app)


class MacSystemCommandTests(unittest.TestCase):
    def test_shutting_down_through_applescript_always_asks(self):
        from src.router.action_router import ActionRouter
        from src.tools.registry import build_default_registry
        profile = {"system": {"bye": {"command": "/usr/bin/osascript -e 'tell application \"System Events\" to shut down'"},
                              "nap": {"command": "/usr/bin/pmset displaysleepnow"}}}
        router = ActionRouter()
        with mock.patch("src.core.config_store.load_profile", return_value=profile):
            router.profile = profile
            reg = build_default_registry(router, config={"tools": {"groups": {}}}, apply_groups=False)
        spec = reg.get("system_command")
        self.assertTrue(spec.always_ask({"command": "bye"}))
        self.assertFalse(spec.always_ask({"command": "nap"}))


if __name__ == "__main__":
    unittest.main()
