"""Install fish-speech, Fish Audio's open-source voice engine, into its own Python environment, and point Jarvis at it.

The Fish Audio voice (tts.engine "fish") speaks from Fish Audio's cloud by default. With tts.fish.mode "local" it
runs fish-speech on this computer instead, cloning a reference clip. fish-speech needs torch (several GB), so it
never goes into Jarvis's own Python: this script

  1. downloads the fish-speech source at a pinned commit into models/fish/fish-speech,
  2. makes a virtual environment in models/fish/venv and installs it there (torch with CUDA with --cuda),
  3. downloads the model weights from Hugging Face into models/fish/checkpoints/<model>,
  4. copies the clip to clone (--reference) into models/fish, and
  5. saves the tts.fish settings.

    python scripts/setup_fish.py --reference my_voice.wav --reference-text "What the clip says."
    python scripts/setup_fish.py --cuda --use        # NVIDIA GPU, and switch the voice to local fish-speech
    python scripts/setup_fish.py --model s2-pro      # the 4B model: best, but needs a large GPU (~24 GB)
    python scripts/setup_fish.py --dry-run           # show what would run

The default model, S1-mini (0.5B, about 3.6 GB), is gated on Hugging Face: sign in there, accept its terms at
https://huggingface.co/fishaudio/s1-mini, make a read token and pass it with --hf-token or the HF_TOKEN
environment variable. The weights are for non-commercial use only (CC-BY-NC-SA-4.0 for S1-mini, the Fish Audio
Research License for S2-Pro); fish-speech's code is under the Fish Audio Research License too. Neither is part of
Jarvis. To remove it, delete models/fish in Jarvis's models folder and pick another voice in Settings.
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from setup_chatterbox import find_python, venv_python  # noqa: E402
from src.core import config_store, paths  # noqa: E402

COMMIT = "3578e4e7099ee85464756dab27a3af86b5a21331"       # fish-speech v2.0.0-beta: S1-mini and S2-Pro
SOURCE = f"https://github.com/fishaudio/fish-speech/archive/{COMMIT}.zip"
TORCH_CUDA = ["torch==2.8.0", "torchaudio==2.8.0", "--index-url", "https://download.pytorch.org/whl/cu128"]
MODELS = {"s1-mini": "fishaudio/s1-mini", "s2-pro": "fishaudio/s2-pro"}
CHECK = "import fish_speech, torch; print('ok')"


def folder() -> Path:
    return paths.models_dir() / "fish"


def repo_dir() -> Path:
    return folder() / "fish-speech"


def checkpoint_dir(model: str) -> Path:
    return folder() / "checkpoints" / model


def download_source(dest: Path, url: str = SOURCE) -> None:
    """fish-speech's source tree at COMMIT into dest (the archive's top folder is dropped)."""
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            rel = Path(*Path(info.filename).parts[1:])
            if not rel.parts or info.is_dir():
                continue
            target = (tmp / rel).resolve()
            if tmp.resolve() not in target.parents:
                raise SystemExit(f"Unsafe path in the fish-speech archive: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)


def commands(base_python: str, venv: Path, cuda: bool, model: str) -> list:
    py = str(venv_python(venv))
    fetch = (f"from huggingface_hub import snapshot_download; "
             f"snapshot_download({MODELS[model]!r}, local_dir={str(checkpoint_dir(model))!r})")
    steps = [[base_python, "-m", "venv", str(venv)],
             [py, "-m", "pip", "install", "--upgrade", "pip"]]
    if cuda:
        steps.append([py, "-m", "pip", "install", *TORCH_CUDA])
    steps += [[py, "-m", "pip", "install", "-e", str(repo_dir())],
              [py, "-c", CHECK],
              [py, "-c", fetch]]
    return steps


def settings(venv: Path, cuda: bool, model: str, reference: str = "", reference_text: str = "",
             use: bool = False) -> dict:
    updates = {"tts.fish.python": str(venv_python(venv)),
               "tts.fish.repo": str(repo_dir()),
               "tts.fish.checkpoint": str(checkpoint_dir(model)),
               "tts.fish.device": "cuda" if cuda else "cpu"}
    if reference:
        updates["tts.fish.reference_audio"] = reference
        updates["tts.fish.reference_text"] = reference_text
    if use:
        updates["tts.engine"] = "fish"
        updates["tts.fish.mode"] = "local"
    return updates


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", default="", help="Python 3.10-3.13 to build the environment with")
    ap.add_argument("--cuda", action="store_true", help="install torch with CUDA 12.8 (NVIDIA GPU)")
    ap.add_argument("--model", choices=sorted(MODELS), default="s1-mini", help="the weights to download")
    ap.add_argument("--hf-token", default="", help="Hugging Face read token (S1-mini is gated; or set HF_TOKEN)")
    ap.add_argument("--reference", default="", help="a clean 10-30 s WAV/MP3 of the voice to clone")
    ap.add_argument("--reference-text", default="", help="the exact words spoken in the reference clip")
    ap.add_argument("--use", action="store_true", help="switch the voice to fish-speech on this computer afterwards")
    ap.add_argument("--dry-run", action="store_true", help="print the steps and settings, change nothing")
    args = ap.parse_args(argv)

    reference = ""
    if args.reference:
        src = Path(args.reference).expanduser()
        if not src.is_file():
            raise SystemExit(f"Reference clip not found: {src}")
        reference = str(folder() / ("reference" + src.suffix.lower()))
    if args.use and not (reference or (config_store.load_config().get("tts", {}).get("fish", {}) or {})
                         .get("reference_audio")):
        raise SystemExit("--use needs a clip to clone: pass --reference <clip> --reference-text \"...\"")

    venv = folder() / "venv"
    base = find_python(args.python)
    steps = commands(base, venv, args.cuda, args.model)
    updates = settings(venv, args.cuda, args.model, reference, args.reference_text, args.use)
    print(f"fish-speech {COMMIT[:8]} into {folder()} (about {'6' if args.cuda else '4'} GB of packages, plus "
          f"{'11' if args.model == 's2-pro' else '3.6'} GB of weights)")
    print(f"  > download {SOURCE} -> {repo_dir()}")
    for step in steps:
        print("  > " + subprocess.list2cmdline(step))
    if args.dry_run:
        print("Settings it would save: " + ", ".join(f"{k}={v}" for k, v in updates.items()))
        return 0
    folder().mkdir(parents=True, exist_ok=True)
    download_source(repo_dir())
    env = dict(os.environ)
    if args.hf_token:
        env["HF_TOKEN"] = args.hf_token               # through the environment, not the command line
    for step in steps:
        if subprocess.run(step, env=env, creationflags=0).returncode != 0:   # in the user's terminal: progress shows
            print(f"FAILED: {subprocess.list2cmdline(step)}")
            if step is steps[-1] and args.model == "s1-mini":
                print("S1-mini is gated: accept its terms at https://huggingface.co/fishaudio/s1-mini and pass "
                      "--hf-token (a read token from https://huggingface.co/settings/tokens).")
            return 1
    if reference:
        shutil.copyfile(Path(args.reference).expanduser(), reference)
    config_store.update_config(updates)
    print("fish-speech is installed." + (" The voice is now fish-speech on this computer; restart the assistant."
                                         if args.use else " Pick it in Settings › Voice (Fish Audio: on this computer)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
