"""The diagnostic report: complete enough to debug from, and safe to attach to a public issue."""
import os
import unittest
from unittest import mock

from src.core import diagnose


class DiagnoseTests(unittest.TestCase):
    def test_report_sections_and_masking(self):
        home = os.path.expanduser("~")
        log = [f"2026-10-23 [INFO] opened {home}\\Documents\\notes.txt",
               "2026-10-23 [INFO] mail to alex@example.com sent",
               "2026-10-23 [INFO] key sk-ant-abcdefghijklmnopqrstuvwxyz0123 used"]  # export-scan: ignore
        with mock.patch("src.core.logging_setup.tail", return_value=log), \
                mock.patch("src.core.winproc.run_hidden", side_effect=FileNotFoundError("no claude")):
            report = diagnose.build_report(log_lines=3)
        for title in ("System", "Packages", "Hardware", "Settings", "Models on disk", "Last 3 log lines"):
            self.assertIn(f"== {title}", report)
        self.assertIn("AI main: claude_code", report)
        self.assertIn("%USERPROFILE%\\Documents\\notes.txt", report)
        self.assertNotIn(home, report)
        self.assertNotIn("alex@example.com", report)
        self.assertNotIn("sk-ant-abcdefghijklmnop", report)  # export-scan: ignore
        self.assertIn("(unavailable: FileNotFoundError", report, "a failing probe doesn't stop the report")

    def test_saved_in_the_data_folder(self):
        with mock.patch.object(diagnose, "build_report", return_value="report"):
            path = diagnose.save_report()
        self.assertEqual(path.parent.name, "reports")
        self.assertEqual(path.read_text(encoding="utf-8"), "report")


if __name__ == "__main__":
    unittest.main()
