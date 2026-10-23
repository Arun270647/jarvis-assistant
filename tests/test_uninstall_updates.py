"""Uninstall (src/app/uninstall.py) and the update check (src/app/updates.py). Nothing is really deleted here:
the cleanup script is written and inspected, never run."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.app import uninstall, updates
from src.core import paths


class UninstallTests(unittest.TestCase):
    def test_plan_without_data_keeps_everything_personal(self):
        with mock.patch("src.core.startup.is_startup_enabled", return_value=True):
            p = uninstall.plan(False)
        self.assertTrue(p["startup"])
        self.assertEqual((p["folders"], p["credentials"]), ([], []))
        self.assertIsNone(p["app_folder"], "a copy run from source never deletes itself")

    def test_plan_with_data(self):
        cfg = {"llm": {"providers": {"openai": {}, "anthropic": {}}}, "email": {"username": "sam@example.com"}}
        p = uninstall.plan(True, cfg)
        self.assertEqual(p["folders"], [str(paths.home())], "with JARVIS_HOME one folder holds everything")
        self.assertEqual(sorted(p["credentials"]), ["JARVIS/LLM/anthropic", "JARVIS/LLM/openai", "JARVIS/SMTP/sam@example.com"])

    def test_never_deletes_a_root_the_home_folder_or_the_app(self):
        self.assertFalse(uninstall.safe_to_delete(Path("C:/")))
        self.assertFalse(uninstall.safe_to_delete(Path.home()))
        self.assertFalse(uninstall.safe_to_delete(paths.APP_ROOT))
        self.assertFalse(uninstall.safe_to_delete(paths.APP_ROOT.parent))
        self.assertTrue(uninstall.safe_to_delete(Path(tempfile.mkdtemp()) / "Jarvis"))

    def test_run_removes_now_and_schedules_folders(self):
        folder = Path(tempfile.mkdtemp(prefix="jarvis-data-"))
        plan = {"startup": True, "shortcuts": [], "apps_entry": False, "credentials": ["JARVIS/LLM/openai"],
                "folders": [str(folder), str(Path.home())], "app_folder": None}
        with mock.patch("src.core.startup.set_startup") as startup, mock.patch("src.core.credentials.delete") as delete, \
                mock.patch("src.core.winproc.popen_hidden") as popen:
            r = uninstall.run(plan, pid=4242)
        startup.assert_called_once_with(False)
        delete.assert_called_once_with("JARVIS/LLM/openai")
        self.assertEqual(r["deleting_after_exit"], [str(folder)], "the home folder is refused even if asked")
        script = Path(popen.call_args[0][0][-1])
        text = script.read_text(encoding="utf-8")
        script.unlink()
        lines = text.splitlines()
        self.assertLess(lines.index('tasklist /FI "PID eq 4242" 2>nul | find "4242" >nul'),
                        lines.index(f'rmdir /s /q "{folder}"'), "it waits for Jarvis to exit before deleting")
        self.assertIn(f'rmdir /s /q "{folder}"', text)
        self.assertNotIn(f'"{Path.home()}"', text)


class UpdateTests(unittest.TestCase):
    def test_version_order(self):
        order = ["0.1.0.dev0", "0.1.0-alpha.1", "0.1.0-alpha.2", "0.1.0-beta.1", "0.1.0-rc.1", "0.1.0", "0.1.1", "0.2", "1.0.0"]
        self.assertEqual(sorted(order, key=updates.version_key), order)

    def fake(self, payload: bytes):
        return lambda req, timeout=None: io.BytesIO(payload)

    def test_check(self):
        latest = {"version": "0.2.0", "notes": "Better voice", "windows": {"url": "https://example.com/J.zip", "sha256": "x"}}
        r = updates.check("https://example.com/latest.json", "0.1.0", opener=self.fake(json.dumps(latest).encode()))
        self.assertEqual((r["available"], r["version"]), (True, "0.2.0"))
        r = updates.check("https://example.com/latest.json", "0.2.0", opener=self.fake(json.dumps(latest).encode()))
        self.assertFalse(r["available"])
        self.assertIn("isn't set up", updates.check("")["error"])

    def test_download_checks_the_checksum(self):
        data = b"zip bytes" * 100
        asset = {"url": "https://example.com/Jarvis-0.2.0-windows-x64.zip", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        path = updates.download(asset, opener=self.fake(data))
        self.assertEqual(path.read_bytes(), data)
        with self.assertRaises(ValueError):
            updates.download(dict(asset, sha256="0" * 64), opener=self.fake(data))


if __name__ == "__main__":
    unittest.main()
