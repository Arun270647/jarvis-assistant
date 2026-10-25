"""The installed Windows copy: finding the Jarvis folder, getting a downloaded installer ready and running it on
restart (src/app/updates.py), Install for me for a copy that isn't in the usual folder (src/app/install.py), the
start-up shortcut and what uninstall deletes. A fake Jarvis folder stands in for the installed one; nothing is
installed, registered or started for real."""
import hashlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.app import install, uninstall, updates
from src.core import paths, startup

SETUP = b"MZ" + b"\0" * 64                  # enough of a Windows program for the updater's check


def fake_jarvis(folder: Path, version: str = "0.1.0") -> Path:
    for rel in ("Jarvis.exe", "runtime/pythonw.exe", "app/jarvis_gui.pyw", "LICENSE"):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(b"x")
    (folder / "app" / "src").mkdir(parents=True, exist_ok=True)
    (folder / "app" / "src" / "version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    return folder


class InstallRootTests(unittest.TestCase):
    def test_found_only_in_the_installed_layout(self):
        root = fake_jarvis(Path(tempfile.mkdtemp(prefix="jarvis-app-")) / "Jarvis")
        self.addCleanup(shutil.rmtree, root.parent, True)
        with mock.patch.object(paths, "APP_ROOT", root / "app"):
            self.assertEqual(paths.install_root(), root)
        with mock.patch.object(paths, "APP_ROOT", root.parent / "source"):
            self.assertIsNone(paths.install_root(), "a source checkout is not an installed copy")
        self.assertIsNone(paths.install_root(), "this checkout runs from source")


@unittest.skipUnless(sys.platform == "win32", "the installed Windows copy (test_macos.py covers Jarvis.app)")
class InstalledCopyTests(unittest.TestCase):
    def setUp(self):
        self.root = fake_jarvis(Path(tempfile.mkdtemp(prefix="jarvis-app-")) / "Jarvis")
        self.addCleanup(shutil.rmtree, self.root.parent, True)
        self.addCleanup(shutil.rmtree, updates.staging_dir(), True)
        patcher = mock.patch.object(paths, "install_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def downloaded(self, name: str = "Jarvis-Setup-0.2.0-windows-x64.exe", data: bytes = SETUP) -> Path:
        updates.staging_dir().mkdir(parents=True, exist_ok=True)
        path = updates.staging_dir() / name
        path.write_bytes(data)
        return path

    def test_updates_install_into_this_folder(self):
        self.assertTrue(updates.updater_available())

    def test_a_downloaded_installer_is_offered_and_kept_until_it_has_run(self):
        setup = self.downloaded()
        self.assertEqual(updates.stage(setup, version="0.2.0"), setup)
        self.assertTrue(updates.ready())
        with mock.patch.object(updates, "__version__", "0.1.0"):
            self.assertEqual(updates.staged_version(), "0.2.0")
            self.assertIsNone(updates.after_start(), "no update ran")
            self.assertTrue(setup.exists(), "a newer version waiting for a restart is kept")
        with mock.patch.object(updates, "__version__", "0.2.0"):
            self.assertIsNone(updates.staged_version())
            updates.after_start()
            self.assertFalse(setup.exists(), "once installed it is cleaned up")

    def test_the_version_comes_from_the_name_when_not_given(self):
        updates.stage(self.downloaded())
        with mock.patch.object(updates, "__version__", "0.1.0"):
            self.assertEqual(updates.staged_version(), "0.2.0")

    def test_a_download_that_isnt_a_program_is_refused(self):
        bad = self.downloaded(data=b"PK\x03\x04 a zip")
        with self.assertRaises(ValueError):
            updates.stage(bad, version="0.2.0")
        self.assertFalse(bad.exists())
        self.assertFalse(updates.ready())

    def test_prepare_downloads_checks_and_keeps_the_installer(self):
        asset = {"url": "https://example.com/Jarvis-Setup-0.2.0-windows-x64.exe",
                 "sha256": hashlib.sha256(SETUP).hexdigest(), "size": len(SETUP), "version": "0.2.0"}
        setup = updates.prepare(asset, opener=lambda req, timeout=None: io.BytesIO(SETUP))
        self.assertTrue(setup.is_file())
        with mock.patch.object(updates, "__version__", "0.1.0"):
            self.assertEqual(updates.staged_version(), "0.2.0")

    def test_after_an_update_the_result_is_reported_once(self):
        updates.result_file().write_text("﻿updated\n", encoding="utf-8")
        self.assertEqual(updates.after_start(), "updated")
        self.assertIsNone(updates.after_start())

    def test_restart_runs_the_installer_silently_after_this_copy_quits_then_starts_jarvis(self):
        setup = self.downloaded()
        updates.stage(setup, version="0.2.0")
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            updates.launch_updater(pid=4242)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[:3], ["cmd", "/d", "/c"])
        script = Path(cmd[3])
        self.assertEqual(script.parent, Path(tempfile.gettempdir()))
        lines = script.read_text(encoding="utf-8").splitlines()
        script.unlink()
        wait = lines.index('tasklist /FI "PID eq 4242" 2>nul | find "4242" >nul')
        run = lines.index(f'"{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /DIR="{self.root}"')
        start = lines.index(f'start "" "{self.root / "Jarvis.exe"}"')
        self.assertLess(wait, run, "the installer runs only after this copy quit")
        self.assertLess(run, start)
        self.assertTrue(any(str(updates.result_file()) in line and "updated" in line for line in lines))

    def test_no_update_ready_means_no_updater(self):
        with self.assertRaises(RuntimeError):
            updates.launch_updater()

    def test_start_up_and_shortcuts_start_jarvis_exe(self):
        c = startup.launch_command()
        self.assertEqual((c["program"], c["arguments"], c["working_dir"]), (str(self.root / "Jarvis.exe"), "", str(self.root)))
        with mock.patch.object(paths, "install_root", return_value=None):
            self.assertIn("jarvis_gui.pyw", startup.launch_command()["arguments"], "from source: pythonw and the script")

    def test_uninstall_deletes_the_whole_jarvis_folder_but_nothing_else(self):
        with mock.patch("src.core.startup.is_startup_enabled", return_value=False):
            p = uninstall.plan(False)
        self.assertEqual(p["app_folder"], str(self.root))
        self.assertTrue(uninstall.installed_copy())
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            r = uninstall.run(dict(p, app_folder=str(self.root.parent)), pid=1)     # a tampered plan
        self.assertEqual(r["deleting_after_exit"], [])
        popen.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Install for me is Windows-only")
class InstallForMeTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="jarvis-install-"))
        self.addCleanup(shutil.rmtree, base, True)
        self.source = fake_jarvis(base / "Downloads" / "Jarvis")
        self.dest = base / "Programs" / "Jarvis"
        for patcher in (mock.patch.object(paths, "install_root", return_value=self.source),
                        mock.patch.object(install, "target", return_value=self.dest),
                        mock.patch.object(install, "start_menu_shortcut", return_value=base / "Start Menu" / "Jarvis.lnk"),
                        mock.patch.object(install, "desktop_shortcut", return_value=base / "Desktop" / "Jarvis.lnk"),
                        mock.patch.object(install, "_register"),
                        mock.patch("src.platform.windows.WindowsPlatform.create_shortcut"),
                        mock.patch("src.core.startup.is_startup_enabled", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_offered_only_when_running_outside_the_usual_folder(self):
        self.assertEqual(install.state(), {"available": True, "installed": False, "from": str(self.source), "to": str(self.dest)})
        with mock.patch.object(paths, "install_root", return_value=self.dest):
            self.assertEqual(install.state()["installed"], True)
            self.assertFalse(install.state()["available"])
        with mock.patch.object(paths, "install_root", return_value=None):
            self.assertFalse(install.state()["available"], "not from source")

    def test_copies_adds_shortcuts_registers_and_restarts_from_the_new_folder(self):
        (self.source / "__pycache__").mkdir()
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            r = install.install(desktop=True, remove_copy=True, pid=4242)
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.dest / "runtime" / "pythonw.exe").exists())
        self.assertFalse((self.dest / "__pycache__").exists(), "caches aren't copied")
        self.assertEqual(len(r["shortcuts"]), 2)
        install._register.assert_called_once_with(self.dest)
        script = Path(popen.call_args[0][0][-1])
        lines = script.read_text(encoding="utf-8").splitlines()
        script.unlink()
        wait = lines.index('tasklist /FI "PID eq 4242" 2>nul | find "4242" >nul')
        self.assertLess(wait, lines.index(f'start "" "{self.dest / "Jarvis.exe"}"'), "starts only after this copy quit")
        self.assertIn(f'rmdir /s /q "{self.source}"', lines)

    def test_never_overwrites_a_folder_that_isnt_jarvis(self):
        self.dest.mkdir(parents=True)
        (self.dest / "notes.txt").write_text("mine", encoding="utf-8")
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            r = install.install()
        self.assertFalse(r["ok"])
        self.assertTrue((self.dest / "notes.txt").exists())
        popen.assert_not_called()

    def test_is_jarvis_folder_refuses_broad_folders(self):
        self.assertTrue(install.is_jarvis_folder(self.source))
        self.assertFalse(install.is_jarvis_folder(self.source.parent))
        self.assertFalse(install.is_jarvis_folder(Path.home()))


if __name__ == "__main__":
    unittest.main()
