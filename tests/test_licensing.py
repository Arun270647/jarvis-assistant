"""Apache-2.0 obligations: the license files are there, nothing GPL is a core requirement, the Kokoro and Piper
engines are add-ons installed into their own folders, and every core requirement is in THIRD_PARTY_LICENSES.md."""
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from src.core import addons, models, paths

ROOT = Path(__file__).resolve().parents[1]


def requirement_names(path: Path) -> list:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.split(r"[<>=!~\[; ]", line, 1)[0].lower())
    return names


class LicenseFileTests(unittest.TestCase):
    def test_the_apache_license_is_the_official_text(self):
        digest = hashlib.sha256((ROOT / "LICENSE").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(digest, "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30")

    def test_notice_and_font_license_ship(self):
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("Apache License, Version 2.0", notice)
        self.assertIn("piper-tts", notice)
        self.assertIn("kokoro-onnx", notice)
        ofl = (ROOT / "src" / "gui" / "web" / "fonts" / "OFL.txt").read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", ofl)
        self.assertIn("Barlow", ofl)

    def test_no_gpl_package_is_a_core_requirement(self):
        names = requirement_names(ROOT / "requirements.txt")
        for gpl in ("piper-tts", "kokoro-onnx", "phonemizer", "phonemizer-fork", "espeakng-loader"):
            self.assertNotIn(gpl, names)
        locked = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        for gpl in ("piper-tts==", "kokoro-onnx==", "phonemizer==", "phonemizer-fork==", "espeakng-loader=="):
            self.assertNotIn("\n" + gpl, locked, "nothing in the lock pulls in a GPL voice engine")
        listing = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
        core = listing.split("## Python packages", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in core.splitlines() if line.startswith("| ") and "GPL" in line and "LGPL" not in line]
        self.assertEqual(rows, [], "a GPL package among the core dependencies")

    def test_every_core_requirement_is_in_the_third_party_list(self):
        listing = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8").lower()
        for name in requirement_names(ROOT / "requirements.txt"):
            self.assertIn(f"| {name} |", listing.replace("_", "-"), f"run python scripts/third_party_licenses.py ({name})")


class KokoroAddOnTests(unittest.TestCase):
    def test_kokoro_is_the_default_voice_and_every_kokoro_voice_needs_the_add_on(self):
        spec = models.catalogue()["addons"]["kokoro"]
        self.assertEqual(spec["module"], "kokoro_onnx")
        pinned = {p.split("==")[0]: p for p in spec["packages"]}
        self.assertTrue({"kokoro-onnx", "phonemizer", "espeakng-loader"} <= set(pinned), spec["packages"])
        self.assertTrue(all("==" in p for p in spec["packages"]), "pinned, so the add-on installs the same everywhere")
        self.assertIn("GPL", spec["license"])
        tts = models.group("tts")
        kokoro = [o for o in tts["options"] if o.get("config", {}).get("tts.engine") == "kokoro"]
        self.assertTrue(kokoro)
        self.assertTrue(all(o.get("addon") == "kokoro" for o in kokoro))
        self.assertEqual([r["option"] for r in tts["recommend"]], ["kokoro-af_heart"], "the wizard recommends Kokoro")

    def test_the_default_settings_load_the_file_the_catalogue_downloads(self):
        defaults = json.loads((ROOT / "config" / "defaults.json").read_text(encoding="utf-8"))["tts"]
        self.assertEqual((defaults["engine"], defaults["fallback"], defaults["chatterbox"]["fallback"]),
                         ("kokoro", "system", "kokoro"))
        _, heart = models.find("kokoro-af_heart")
        self.assertEqual(heart["config"]["tts.kokoro.voice"], defaults["kokoro"]["voice"])
        self.assertEqual({f["name"] for f in heart["files"]}, {defaults["kokoro"]["model"], "voices-v1.0.bin"})
        self.assertEqual(heart["dir"], defaults["kokoro"]["model_dir"])


class PiperAddOnTests(unittest.TestCase):
    def test_the_catalogue_names_the_add_on_and_every_piper_voice_needs_it(self):
        spec = models.catalogue()["addons"]["piper"]
        self.assertEqual(spec["module"], "piper")
        self.assertTrue(spec["packages"][0].startswith("piper-tts=="))
        self.assertIn("GPL", spec["license"])
        tts = models.group("tts")
        piper = [o for o in tts["options"] if o.get("config", {}).get("tts.engine") == "piper"]
        self.assertTrue(piper)
        self.assertTrue(all(o.get("addon") == "piper" for o in piper))

    def test_install_runs_pip_into_the_models_folder_without_dependencies(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("src.core.winproc.run_hidden", return_value=done) as run, \
                mock.patch.object(addons, "installed", return_value=True):
            addons.install("piper")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--no-deps", cmd)
        self.assertEqual(cmd[cmd.index("--target") + 1], str(paths.models_dir() / "addons" / "piper"))
        self.assertIn("piper-tts==1.8.0", cmd)

    def test_a_failed_install_says_what_pip_said(self):
        failed = subprocess.CompletedProcess([], 1, "", "ERROR: No matching distribution found for piper-tts\n")
        with mock.patch("src.core.winproc.run_hidden", return_value=failed):
            with self.assertRaises(RuntimeError) as ctx:
                addons.install("piper")
        self.assertIn("No matching distribution", str(ctx.exception))

    def test_activate_appends_the_folder_once(self):
        folder = addons.folder("piper")
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: sys.path.remove(str(folder)) if str(folder) in sys.path else None)
        addons.activate("piper")
        addons.activate("piper")
        self.assertEqual(sys.path.count(str(folder)), 1)
        self.assertEqual(sys.path[-1], str(folder), "Jarvis's own packages come first")


if __name__ == "__main__":
    unittest.main()
