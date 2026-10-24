"""Setup script for Piper TTS.

Downloads Piper voice models from HuggingFace and, when it's missing, the Piper engine (piper-tts). The engine is
GPL-3.0 and not part of Jarvis (Apache-2.0): it is installed from PyPI into models/addons/piper, see
src/core/addons.py. The setup wizard and Settings › Models do the same when a Piper voice is chosen.

Usage:
    python scripts/setup_piper.py                           # install default voices (and the engine)
    python scripts/setup_piper.py --engine                  # install only the engine
    python scripts/setup_piper.py --voice en_US-ryan-high   # install specific voice
    python scripts/setup_piper.py --list                    # list available voices
    python scripts/setup_piper.py --list-downloaded         # list downloaded voices
    python scripts/setup_piper.py --set-voice en_US-amy-medium  # change the active voice in the settings

Voices go to models/piper in Jarvis's models folder (src.core.paths.models_dir()).
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import config_store, paths  # noqa: E402
from src.core.config_store import load_config  # noqa: E402


# Voice catalog: HuggingFace rhasspy/piper-voices repository structure
# URL pattern: {base}/{lang}/{locale}/{voice}/{quality}/{locale}-{voice}-{quality}.onnx
VOICES = {
    "en_US-ryan-high": {
        "lang": "en",
        "locale": "en_US",
        "voice": "ryan",
        "quality": "high",
        "description": "US English, male voice (Ryan) - high quality",
    },
    "en_US-amy-medium": {
        "lang": "en",
        "locale": "en_US",
        "voice": "amy",
        "quality": "medium",
        "description": "US English, female voice (Amy) - medium quality",
    },
    "en_US-lessac-medium": {
        "lang": "en",
        "locale": "en_US",
        "voice": "lessac",
        "quality": "medium",
        "description": "US English, male voice (Lessac) - medium quality",
    },
    "en_GB-alan-medium": {
        "lang": "en",
        "locale": "en_GB",
        "voice": "alan",
        "quality": "medium",
        "description": "British English, male voice (Alan) - medium quality",
    },
}


def build_voice_url(voice_id: str, base_url: str) -> tuple[str, str]:
    """Build HuggingFace URLs for voice model and config.

    Returns (model_url, config_url)
    """
    if voice_id not in VOICES:
        raise ValueError(f"Unknown voice: {voice_id}")

    v = VOICES[voice_id]
    # URL: {base}/{lang}/{locale}/{voice}/{quality}/{locale}-{voice}-{quality}.onnx
    path = f"{v['lang']}/{v['locale']}/{v['voice']}/{v['quality']}/{voice_id}.onnx"
    model_url = f"{base_url}/{path}"
    config_url = f"{model_url}.json"

    return model_url, config_url


def download_file(url: str, dest: Path):
    """Download a file with progress."""
    print(f"  Downloading: {url}")
    print(f"  To: {dest}")

    def reporthook(count, block_size, total_size):
        if total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            sys.stdout.write(f"\r  Progress: {percent}%")
            sys.stdout.flush()
        else:
            sys.stdout.write(f"\r  Downloaded: {count * block_size} bytes")
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, dest, reporthook=reporthook)
        print("\n  Done")
        return True
    except urllib.error.HTTPError as e:
        print(f"\n  ERROR: HTTP {e.code} - {e.reason}")
        print(f"  URL: {url}")
        return False
    except Exception as e:
        print(f"\n  ERROR: {e}")
        return False


def get_model_dir() -> Path:
    """The Piper voice folder (tts.piper.model_dir, relative to the models folder)."""
    cfg = load_config()
    return paths.model_path(cfg.get("tts", {}).get("piper", {}).get("model_dir") or "piper")


def get_base_url() -> str:
    """Get HuggingFace base URL from config."""
    cfg = load_config()
    return cfg.get("tts", {}).get("piper", {}).get(
        "huggingface_base_url",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    )


def install_voice(voice_id: str) -> bool:
    """Download and install a Piper voice model."""
    if voice_id not in VOICES:
        print(f"ERROR: Unknown voice: {voice_id}")
        print(f"Available voices: {', '.join(VOICES.keys())}")
        return False

    voice = VOICES[voice_id]
    model_dir = get_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    base_url = get_base_url()

    model_path = model_dir / f"{voice_id}.onnx"
    config_path = model_dir / f"{voice_id}.onnx.json"

    print(f"\n=== Installing Piper Voice: {voice_id} ===")
    print(f"Description: {voice['description']}")
    print(f"Quality: {voice['quality']}")
    print(f"Model dir: {model_dir}")

    # Build URLs
    model_url, config_url = build_voice_url(voice_id, base_url)

    # Download model
    if model_path.exists():
        print(f"\nModel already exists: {model_path}")
    else:
        print("\nDownloading model...")
        if not download_file(model_url, model_path):
            return False

    # Download config
    if config_path.exists():
        print(f"\nConfig already exists: {config_path}")
    else:
        print("\nDownloading config...")
        if not download_file(config_url, config_path):
            return False

    print("\n=== Installation Complete ===")
    print(f"Model: {model_path}")
    print(f"Config: {config_path}")

    return True


def list_voices():
    """List all available Piper voices."""
    print("\n=== Available Piper Voices ===\n")
    model_dir = get_model_dir()

    for voice_id, info in VOICES.items():
        installed = (model_dir / f"{voice_id}.onnx").exists()
        status = "[INSTALLED]" if installed else ""
        print(f"  {voice_id:30} {info['quality']:8} {status}")
        print(f"    {info['description']}")
    print()


def list_downloaded():
    """List downloaded voices in model directory."""
    model_dir = get_model_dir()

    if not model_dir.exists():
        print(f"\nModel directory not found: {model_dir}")
        return

    onnx_files = list(model_dir.glob("*.onnx"))

    if not onnx_files:
        print(f"\nNo voices downloaded in: {model_dir}")
        return

    print("\n=== Downloaded Voices ===")
    print(f"Directory: {model_dir}\n")

    cfg = load_config()
    active_voice = cfg.get("tts", {}).get("piper", {}).get("voice", "")

    for onnx_file in sorted(onnx_files):
        voice_id = onnx_file.stem  # filename without .onnx
        config_file = onnx_file.with_suffix(".onnx.json")
        has_config = config_file.exists()
        is_active = (voice_id == active_voice)

        status = []
        if is_active:
            status.append("ACTIVE")
        if not has_config:
            status.append("MISSING CONFIG")

        status_str = f" [{', '.join(status)}]" if status else ""

        size_mb = onnx_file.stat().st_size / 1024 / 1024
        print(f"  {voice_id:30} {size_mb:6.1f} MB{status_str}")

    print()


def set_active_voice(voice_id: str) -> bool:
    """Make voice_id the active Piper voice (tts.piper.voice in the user's settings)."""
    model_dir = get_model_dir()
    model_path = model_dir / f"{voice_id}.onnx"

    if not model_path.exists():
        print(f"ERROR: Voice not downloaded: {voice_id}")
        print(f"Run: python scripts/setup_piper.py --voice {voice_id}")
        return False

    config_store.update_config({"tts.piper.voice": voice_id})

    print(f"\n[OK] Active voice set to: {voice_id}")
    print(f"Settings updated: {paths.config_file()}")
    return True


def check_piper_binary():
    """Make sure the Piper engine add-on is importable, installing it when it's missing."""
    from src.core import addons
    if addons.installed("piper"):
        print("[OK] Piper engine available")
        return True
    spec = addons.spec("piper")
    print(f"Installing {spec['name']} ({', '.join(spec['packages'])}, {spec['license']}) into {addons.folder('piper')}")
    try:
        addons.install("piper")
    except RuntimeError as e:
        print(f"[WARN] {e}")
        return False
    print("[OK] Piper engine installed")
    return True


def main():
    print("=" * 60)
    print("  Piper TTS Setup for Jarvis")
    print("=" * 60)

    if "--list" in sys.argv:
        list_voices()
        return 0

    if "--list-downloaded" in sys.argv:
        list_downloaded()
        return 0

    if "--set-voice" in sys.argv:
        idx = sys.argv.index("--set-voice")
        if idx + 1 >= len(sys.argv):
            print("ERROR: --set-voice requires a voice name")
            return 1
        voice_id = sys.argv[idx + 1]
        return 0 if set_active_voice(voice_id) else 1

    # The engine (an add-on, not shipped with Jarvis)
    engine_ok = check_piper_binary()
    print()
    if "--engine" in sys.argv:
        return 0 if engine_ok else 1

    # Determine which voice(s) to install
    voices_to_install = []

    if "--voice" in sys.argv:
        idx = sys.argv.index("--voice")
        if idx + 1 < len(sys.argv):
            voices_to_install.append(sys.argv[idx + 1])

    if not voices_to_install:
        # Default: the voice in config/defaults.json first (it becomes the active one), plus ryan-high
        voices_to_install = ["en_US-amy-medium", "en_US-ryan-high"]
        print("Installing default voices: en_US-amy-medium, en_US-ryan-high")

    # Install
    success_count = 0
    for voice_id in voices_to_install:
        if install_voice(voice_id):
            success_count += 1
        print()

    if success_count > 0:
        # Set first installed voice as active
        set_active_voice(voices_to_install[0])
        print("Switch voices with: python scripts/setup_piper.py --set-voice <voice_id>")
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
