"""Install the optional Chatterbox voice into its own Python environment, and point Jarvis at it.

Chatterbox sounds the most natural but needs torch (several GB), so it never goes into Jarvis's own Python:
this script makes a separate virtual environment in the models folder, installs chatterbox-tts there, checks
that it imports, and saves tts.chatterbox.python (plus folders for its downloaded weights) in the settings.

    python scripts/setup_chatterbox.py                 # CPU (slow: answers start after ~10 s)
    python scripts/setup_chatterbox.py --cuda          # NVIDIA GPU: torch with CUDA 12.4
    python scripts/setup_chatterbox.py --use           # also switch the voice to Chatterbox
    python scripts/setup_chatterbox.py --python C:\\Python311\\python.exe
    python scripts/setup_chatterbox.py --dry-run       # show what would run

Needs Python 3.10–3.13 for the environment (found with the py launcher if not given). To remove it, delete
models/chatterbox in Jarvis's models folder and pick another voice in Settings.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import config_store, paths, winproc  # noqa: E402

PACKAGE = "chatterbox-tts==0.1.7"
TORCH_CUDA = ["torch==2.6.0", "torchaudio==2.6.0", "--index-url", "https://download.pytorch.org/whl/cu124"]
CHECK = "from chatterbox.tts_turbo import ChatterboxTurboTTS; print('ok')"
SUPPORTED = ((3, 10), (3, 11), (3, 12), (3, 13))


def folder() -> Path:
    return paths.models_dir() / "chatterbox"


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def python_version(exe: str) -> tuple:
    try:
        out = winproc.run_hidden([exe, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                                 capture_output=True, text=True, timeout=30).stdout.split()
        return int(out[0]), int(out[1])
    except Exception:
        return ()


def find_python(given: str = "") -> str:
    """A Python 3.10–3.13 for the environment: --python, the py launcher (3.11 first: Chatterbox's own
    target), or the one running this script."""
    candidates = [given] if given else []
    if sys.platform == "win32" and not given:
        for v in ("3.11", "3.12", "3.10", "3.13"):
            try:
                out = winproc.run_hidden(["py", f"-{v}", "-c", "import sys; print(sys.executable)"],
                                         capture_output=True, text=True, timeout=30)
                if out.returncode == 0 and out.stdout.strip():
                    candidates.append(out.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                break
    if not given:
        candidates.append(sys.executable)
    for exe in candidates:
        if python_version(exe) in SUPPORTED:
            return exe
    raise SystemExit("No Python 3.10–3.13 found. Install Python 3.11 from python.org, or pass --python <path>.")


def commands(base_python: str, venv: Path, cuda: bool) -> list:
    py = str(venv_python(venv))
    steps = [[base_python, "-m", "venv", str(venv)],
             [py, "-m", "pip", "install", "--upgrade", "pip"]]
    if cuda:
        steps.append([py, "-m", "pip", "install", *TORCH_CUDA])
    steps += [[py, "-m", "pip", "install", PACKAGE], [py, "-c", CHECK]]
    return steps


def settings(venv: Path, cuda: bool, use: bool) -> dict:
    updates = {"tts.chatterbox.python": str(venv_python(venv)),
               "tts.chatterbox.hf_home": str(folder() / "hf"),
               "tts.chatterbox.torch_home": str(folder() / "torch"),
               "tts.chatterbox.device": "cuda" if cuda else "cpu"}
    if use:
        updates["tts.engine"] = "chatterbox"
    return updates


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", default="", help="Python 3.10-3.13 to build the environment with")
    ap.add_argument("--cuda", action="store_true", help="install torch with CUDA 12.4 (NVIDIA GPU)")
    ap.add_argument("--use", action="store_true", help="switch the voice to Chatterbox afterwards")
    ap.add_argument("--dry-run", action="store_true", help="print the steps and settings, change nothing")
    args = ap.parse_args(argv)

    venv = folder() / "venv"
    base = find_python(args.python)
    steps = commands(base, venv, args.cuda)
    updates = settings(venv, args.cuda, args.use)
    print(f"Chatterbox environment: {venv} (about {'5' if args.cuda else '3'} GB once installed)")
    for step in steps:
        print("  > " + subprocess.list2cmdline(step))
    if args.dry_run:
        print("Settings it would save: " + ", ".join(f"{k}={v}" for k, v in updates.items()))
        return 0
    folder().mkdir(parents=True, exist_ok=True)
    for step in steps:
        if subprocess.run(step, creationflags=0).returncode != 0:      # in the user's terminal: pip's progress shows
            print(f"FAILED: {subprocess.list2cmdline(step)}")
            return 1
    config_store.update_config(updates)
    print("Chatterbox is installed." + (" The voice is now Chatterbox; restart the assistant." if args.use else
                                        " Pick it in Settings › Voice."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
