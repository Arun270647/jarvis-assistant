"""The Windows build's parts (packaging/): the PE import reader that decides which Qt files ship, the slimming of
site-packages shared with the Mac build (run on a fake site-packages with this Python), and the installer script's
promises: per-user, no uninstaller of its own, and the same shortcuts and Settings > Apps entry that Jarvis's
uninstall removes."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import build_windows  # noqa: E402


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


class SlimTests(unittest.TestCase):
    def setUp(self):
        self.site = Path(tempfile.mkdtemp(prefix="jarvis-site-"))
        self.addCleanup(shutil.rmtree, self.site, True)
        files = {
            "av/__init__.py": "import av._core\n", "av.libs/avcodec.dll": "", "av-19.0.dist-info/METADATA": "",
            "hf_xet/__init__.py": "", "numpy/__init__.py": "VALUE = 42\n", "numpy/tests/test_x.py": "this isn't python",
            "onnxruntime/__init__.py": "", "onnxruntime/transformers/big.py": "", "onnxruntime/capi/onnxruntime.dll": "",
            "onnxruntime/capi/libonnxruntime.1.31.0.dylib": "", "onnxruntime/capi/state.pyd": "", "pip/__init__.py": "",
            "pip/__pycache__/__init__.cpython-313.pyc": "", "plain.py": "def answer():\n    return 42\n",
        }
        for rel, text in files.items():
            (self.site / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.site / rel).write_text(text, encoding="utf-8")

    def slim(self):
        return build_windows.slim(self.site, Path(sys.executable))         # this Python stands in for the runtime's

    def test_leaves_out_what_jarvis_never_loads(self):
        self.slim()
        for gone in ("av.libs", "av-19.0.dist-info", "hf_xet", "numpy/tests", "onnxruntime/transformers",
                     "onnxruntime/capi/onnxruntime.dll", "onnxruntime/capi/libonnxruntime.1.31.0.dylib"):
            self.assertFalse((self.site / gone).exists(), gone)
        self.assertTrue((self.site / "onnxruntime" / "capi" / "state.pyd").exists(), "the Python module stays")

    def test_ships_compiled_modules_without_their_source(self):
        self.slim()
        self.assertFalse((self.site / "plain.py").exists())
        self.assertTrue((self.site / "plain.pyc").is_file())
        self.assertFalse(list(self.site.rglob("numpy/__pycache__")))
        self.assertTrue((self.site / "pip" / "__init__.py").is_file(), "pip keeps its source")
        out = subprocess.run([sys.executable, "-c", "import plain, numpy; print(plain.answer(), numpy.VALUE)"],
                             cwd=self.site, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["42", "42"])

    def test_pyav_stand_in_imports_but_has_nothing(self):
        self.slim()
        code = ("import av\nprint(hasattr(av, 'open'))\ntry:\n    from av import open\nexcept ImportError as e:\n"
                "    print('ImportError')\n")
        out = subprocess.run([sys.executable, "-c", code], cwd=self.site, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["False", "ImportError"])


class InstallerScriptTests(unittest.TestCase):
    """packaging/windows/jarvis.iss keeps the promises src/app/install.py and uninstall.py rely on."""

    def setUp(self):
        self.iss = (ROOT / "packaging" / "windows" / "jarvis.iss").read_text(encoding="utf-8")

    def test_installs_for_this_user_only_without_an_uninstaller_of_its_own(self):
        for line in ("PrivilegesRequired=lowest", r"DefaultDirName={userpf}\Jarvis", "Uninstallable=no",
                     "DisableDirPage=yes"):
            self.assertIn(line, self.iss)

    def test_uses_the_shortcuts_and_apps_entry_that_jarvis_uninstall_removes(self):
        from src.app import install, uninstall
        self.assertIn(r'Name: "{userprograms}\Jarvis"', self.iss)
        self.assertEqual(install.start_menu_shortcut().name, "Jarvis.lnk")
        self.assertIn(f'Subkey: "{uninstall.UNINSTALL_KEY}"', self.iss)
        self.assertIn('ValueData: """{app}\\Jarvis.exe"" --uninstall"', self.iss)

    def test_an_update_replaces_the_app_and_runtime_folders(self):
        self.assertIn(r'Type: filesandordirs; Name: "{app}\app"', self.iss)
        self.assertIn(r'Type: filesandordirs; Name: "{app}\runtime"', self.iss)

    def test_installer_name_matches_the_updater(self):
        import re
        from src.app import updates  # noqa: F401  (the updater reads the version back from this name)
        name = build_windows.installer_name("0.1.0-alpha.1") + ".exe"
        self.assertEqual(name, "Jarvis-Setup-0.1.0-alpha.1-windows-x64.exe")
        self.assertEqual(re.search(r"Jarvis-Setup-(.+)-windows", name).group(1), "0.1.0-alpha.1")


if __name__ == "__main__":
    unittest.main()
