"""Running from the Windows zip: finding the Jarvis folder, unpacking and offering an update (src/app/updates.py),
Install for me (src/app/install.py), the start-up shortcut and what uninstall deletes. A fake Jarvis folder stands
in for the zip; nothing is installed, registered or started for real."""
import io
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.app import install, uninstall, updates
from src.core import paths, startup


def fake_jarvis(folder: Path, version: str = "0.1.0") -> Path:
    for rel in ("Jarvis.exe", "Jarvis-Updater.exe", "runtime/pythonw.exe", "app/jarvis_gui.pyw", "LICENSE"):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(b"x")
    (folder / "app" / "src").mkdir(parents=True, exist_ok=True)
    (folder / "app" / "src" / "version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    return folder


def release_zip(version: str, extra: dict = None) -> bytes:
    source = fake_jarvis(Path(tempfile.mkdtemp()) / "Jarvis", version)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in source.rglob("*"):
            if f.is_file():
                z.write(f, "Jarvis/" + f.relative_to(source).as_posix())
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    shutil.rmtree(source.parent)
    return buf.getvalue()


class InstallRootTests(unittest.TestCase):
    def test_found_only_in_the_zip_layout(self):
        root = fake_jarvis(Path(tempfile.mkdtemp(prefix="jarvis-zip-")) / "Jarvis")
        self.addCleanup(shutil.rmtree, root.parent, True)
        with mock.patch.object(paths, "APP_ROOT", root / "app"):
            self.assertEqual(paths.install_root(), root)
        with mock.patch.object(paths, "APP_ROOT", root.parent / "source"):
            self.assertIsNone(paths.install_root(), "a source checkout is not the zip")
        self.assertIsNone(paths.install_root(), "this checkout runs from source")


@unittest.skipUnless(sys.platform == "win32", "the Windows zip's layout (test_macos.py covers Jarvis.app)")
class ZipFolderTests(unittest.TestCase):
    def setUp(self):
        self.root = fake_jarvis(Path(tempfile.mkdtemp(prefix="jarvis-zip-")) / "Jarvis")
        self.addCleanup(shutil.rmtree, self.root.parent, True)
        patcher = mock.patch.object(paths, "install_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_update_is_unpacked_offered_and_kept_until_it_is_installed(self):
        zip_path = self.root.parent / "Jarvis-0.2.0-windows-x64.zip"
        zip_path.write_bytes(release_zip("0.2.0"))
        new = updates.stage(zip_path)
        self.assertEqual(new, self.root / ".update" / "Jarvis")
        self.assertTrue(updates.ready())
        with mock.patch.object(updates, "__version__", "0.1.0"):
            self.assertEqual(updates.staged_version(), "0.2.0")
            self.assertIsNone(updates.after_start(), "no update ran")
            self.assertTrue(updates.ready(), "a newer version waiting for a restart is kept")
        with mock.patch.object(updates, "__version__", "0.2.0"):
            self.assertIsNone(updates.staged_version())
            updates.after_start()
            self.assertFalse((self.root / ".update").exists(), "once installed it is cleaned up")

    def test_an_unsafe_or_incomplete_zip_is_refused(self):
        bad = self.root.parent / "bad.zip"
        bad.write_bytes(release_zip("0.2.0", {"../evil.txt": "x"}))
        with self.assertRaises(ValueError):
            updates.stage(bad)
        self.assertFalse((self.root.parent / "evil.txt").exists())
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("Jarvis/app/jarvis_gui.pyw", "x")
        with self.assertRaises(ValueError):
            updates.stage(bad)
        self.assertFalse(updates.ready())

    def test_prepare_downloads_checks_unpacks_and_deletes_the_zip(self):
        import hashlib
        data = release_zip("0.2.0")
        asset = {"url": "https://example.com/Jarvis-0.2.0-windows-x64.zip", "sha256": hashlib.sha256(data).hexdigest(),
                 "size": len(data)}
        updates.prepare(asset, opener=lambda req, timeout=None: io.BytesIO(data))
        self.assertTrue(updates.ready())
        self.assertFalse((paths.cache_dir() / "updates" / "Jarvis-0.2.0-windows-x64.zip").exists())

    def test_after_an_update_the_result_is_reported_once_and_old_files_go(self):
        (self.root / ".old" / "app").mkdir(parents=True)
        updates.result_file().write_text("﻿updated\n", encoding="utf-8")
        self.assertEqual(updates.after_start(), "updated")
        self.assertFalse((self.root / ".old").exists())
        self.assertIsNone(updates.after_start())

    def test_the_updater_runs_from_a_temporary_copy_with_this_folder_and_process(self):
        zip_path = self.root.parent / "u.zip"
        zip_path.write_bytes(release_zip("0.2.0"))
        updates.stage(zip_path)
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            updates.launch_updater(pid=4242)
        cmd = popen.call_args[0][0]
        self.assertTrue(Path(cmd[0]).name.startswith("Jarvis-Updater-"))
        self.assertEqual(Path(cmd[0]).parent, Path(tempfile.gettempdir()))
        self.assertEqual(cmd[1:], ["--install", str(self.root), "--pid", "4242", "--result", str(updates.result_file())])
        Path(cmd[0]).unlink()

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

    def test_offered_only_when_running_from_the_extracted_zip(self):
        self.assertEqual(install.state(), {"available": True, "installed": False, "from": str(self.source), "to": str(self.dest)})
        with mock.patch.object(paths, "install_root", return_value=self.dest):
            self.assertEqual(install.state()["installed"], True)
            self.assertFalse(install.state()["available"])
        with mock.patch.object(paths, "install_root", return_value=None):
            self.assertFalse(install.state()["available"], "not from source")

    def test_copies_adds_shortcuts_registers_and_restarts_from_the_new_folder(self):
        (self.source / ".update").mkdir()
        with mock.patch("src.core.winproc.popen_hidden") as popen:
            r = install.install(desktop=True, remove_copy=True, pid=4242)
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.dest / "runtime" / "pythonw.exe").exists())
        self.assertFalse((self.dest / ".update").exists(), "a half-downloaded update isn't copied")
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
