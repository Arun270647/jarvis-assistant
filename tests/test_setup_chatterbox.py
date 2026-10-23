"""scripts/setup_chatterbox.py: the steps it runs and the settings it saves (checked with --dry-run; the real
install downloads several GB)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

from src.core import config_store, paths

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_chatterbox  # noqa: E402


class SetupChatterboxTests(unittest.TestCase):
    def test_steps(self):
        venv = paths.models_dir() / "chatterbox" / "venv"
        steps = setup_chatterbox.commands("C:/Python311/python.exe", venv, cuda=True)
        self.assertEqual(steps[0], ["C:/Python311/python.exe", "-m", "venv", str(venv)])
        joined = [" ".join(s) for s in steps]
        self.assertTrue(any("download.pytorch.org/whl/cu124" in s for s in joined), "CUDA torch before chatterbox")
        self.assertIn("chatterbox-tts==0.1.7", joined[-2])
        self.assertIn("ChatterboxTurboTTS", joined[-1])
        self.assertFalse(any("cu124" in s for s in map(" ".join, setup_chatterbox.commands("py", venv, cuda=False))))

    def test_settings_are_valid_and_point_into_the_models_folder(self):
        updates = setup_chatterbox.settings(paths.models_dir() / "chatterbox" / "venv", cuda=False, use=True)
        self.assertEqual(config_store.validate_update(updates), [])
        self.assertEqual(updates["tts.engine"], "chatterbox")
        self.assertTrue(updates["tts.chatterbox.python"].startswith(str(paths.models_dir())))

    def test_dry_run_changes_nothing(self):
        with mock.patch.object(setup_chatterbox, "find_python", return_value="python"), \
                mock.patch("subprocess.run") as run, mock.patch("src.core.winproc.run_hidden"), \
                mock.patch.object(config_store, "update_config") as save:
            self.assertEqual(setup_chatterbox.main(["--dry-run"]), 0)
        run.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
