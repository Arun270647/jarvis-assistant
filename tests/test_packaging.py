"""The Windows build's parts (packaging/): the PE import reader that decides which Qt files ship, and the compiled
updater, run for real on a fake Jarvis folder: it swaps in the new version and keeps the old one, and when a file is
locked it puts everything back. The updater tests need packaging/build_windows.py to have compiled it (they are
skipped otherwise; the build runs them)."""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import build_windows  # noqa: E402

UPDATER = build_windows.WORK / "programs" / "Jarvis-Updater.exe"


class PeImportTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows programs")
    def test_reads_the_dlls_a_program_imports(self):
        python = Path(sys.base_prefix) / "python.exe"
        dll = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        self.assertIn(dll, build_windows.pe_imports(python))
        self.assertIn("kernel32.dll", build_windows.pe_imports(Path(sys.base_prefix) / dll))

    def test_version_numbers_for_the_exe_resources(self):
        self.assertEqual(build_windows.version_numbers("0.1.0"), "0,1,0,0")
        self.assertEqual(build_windows.version_numbers("0.2.1-alpha.3"), "0,2,1,0")
        self.assertEqual(build_windows.version_numbers("0.1.0.dev0"), "0,1,0,0")


@unittest.skipUnless(sys.platform == "win32" and UPDATER.exists(), "needs the updater from packaging/build_windows.py")
class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jarvis-updater-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.install = self.fake_jarvis(self.dir / "Jarvis", "old")
        self.fake_jarvis(self.install / ".update" / "Jarvis", "new")
        self.result = self.dir / "result.txt"

    def fake_jarvis(self, folder: Path, marker: str) -> Path:
        """A Jarvis folder whose Jarvis.exe is a copy of the updater: started without arguments it exits at once,
        so the relaunch opens nothing."""
        for rel in ("runtime/pythonw.exe", "app/jarvis_gui.pyw", "LICENSE"):
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(marker, encoding="utf-8")
        shutil.copy2(UPDATER, folder / "Jarvis.exe")
        shutil.copy2(UPDATER, folder / "Jarvis-Updater.exe")
        return folder

    def run_updater(self, timeout: float = 90) -> str:
        quit_jarvis = subprocess.Popen([sys.executable, "-c", "pass"])      # stands in for Jarvis, which has quit
        quit_jarvis.wait()                  # its handle stays open, so the process id isn't reused meanwhile
        copy = self.dir / "Jarvis-Updater-copy.exe"
        shutil.copy2(UPDATER, copy)
        subprocess.run([str(copy), "--install", str(self.install), "--pid", str(quit_jarvis.pid),
                        "--result", str(self.result)], timeout=timeout)
        deadline = time.time() + 10
        while not self.result.exists() and time.time() < deadline:
            time.sleep(0.1)
        return self.result.read_text(encoding="utf-8-sig").strip()

    def test_swaps_in_the_new_version_and_keeps_the_old_one(self):
        self.assertEqual(self.run_updater(), "updated")
        self.assertEqual((self.install / "app" / "jarvis_gui.pyw").read_text(encoding="utf-8"), "new")
        self.assertEqual((self.install / "runtime" / "pythonw.exe").read_text(encoding="utf-8"), "new")
        self.assertEqual((self.install / ".old" / "app" / "jarvis_gui.pyw").read_text(encoding="utf-8"), "old")
        self.assertEqual(list((self.install / ".update" / "Jarvis").iterdir()), [], "every item moved")

    def test_a_locked_file_puts_everything_back(self):
        with open(self.install / "runtime" / "pythonw.exe", "rb"):          # in use: runtime can't move
            result = self.run_updater()
        self.assertTrue(result.startswith("failed: couldn't replace runtime"), result)
        self.assertIn("Nothing was changed", result)
        for rel in ("app/jarvis_gui.pyw", "runtime/pythonw.exe", "LICENSE"):
            self.assertEqual((self.install / rel).read_text(encoding="utf-8"), "old", rel)
            self.assertEqual((self.install / ".update" / "Jarvis" / rel).read_text(encoding="utf-8"), "new", rel)

    def test_an_incomplete_update_is_refused(self):
        (self.install / ".update" / "Jarvis" / "runtime" / "pythonw.exe").unlink()
        self.assertEqual(self.run_updater(), "failed: the downloaded update is incomplete.")
        self.assertEqual((self.install / "app" / "jarvis_gui.pyw").read_text(encoding="utf-8"), "old")


if __name__ == "__main__":
    unittest.main()
