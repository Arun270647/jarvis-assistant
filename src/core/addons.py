"""Optional add-ons that Jarvis does not ship (config/models.json "addons").

Jarvis is Apache-2.0. Some engines it can use are under licenses that don't allow shipping them inside it: the
Piper voice engine (piper-tts) is GPL-3.0-or-later. Such an engine is an add-on. When the user picks a voice that
needs it, the model downloader installs it from PyPI with pip into its own folder in the models folder
(models/addons/<name>), and the engine puts that folder on sys.path only when it loads. It is never part of the
app folder or its requirements; an update leaves it alone, and "delete my data" removes it with the models.

A copy already importable from Jarvis's own Python (a developer who ran pip install piper-tts) counts as
installed. The packaged app (Phase 6) will need a Python to run pip with; until then this uses sys.executable.
"""
import importlib
import importlib.util
import sys
from pathlib import Path

from src.core import models, paths, winproc


def spec(name: str) -> dict:
    return models.catalogue().get("addons", {})[name]


def folder(name: str) -> Path:
    return paths.models_dir() / "addons" / name


def activate(name: str) -> None:
    """Make the add-on importable: its folder goes at the end of sys.path, so Jarvis's own packages win."""
    path = folder(name)
    if path.is_dir() and str(path) not in sys.path:
        sys.path.append(str(path))
        importlib.invalidate_caches()


def installed(name: str) -> bool:
    activate(name)
    try:
        return importlib.util.find_spec(spec(name)["module"]) is not None
    except (ImportError, ValueError):
        return False


def install(name: str, timeout: float = 900) -> None:
    """pip install the add-on's packages into its folder. No dependencies: Jarvis's own install has them
    (onnxruntime, numpy), and the catalogue lists the few extra ones. Raises RuntimeError on failure."""
    s = spec(name)
    target = folder(name)
    target.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps",
           "--upgrade", "--target", str(target), *s["packages"]]
    try:
        r = winproc.run_hidden(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Couldn't start pip to install {s['name']}: {e}") from e
    if r.returncode != 0:
        tail = " ".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])
        raise RuntimeError(f"Installing {s['name']} failed: {tail or 'pip exited with ' + str(r.returncode)}")
    importlib.invalidate_caches()
    if not installed(name):
        raise RuntimeError(f"{s['name']} was installed but can't be imported from {target}")
