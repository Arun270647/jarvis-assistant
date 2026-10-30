"""The Fish Audio API key for the Fish Audio voice (src/tts/fish.py), in the OS secret store under JARVIS/TTS/fish.

Kept apart from the voice engine so the setup wizard and Settings can check or save the key without loading the
audio libraries. The FISH_API_KEY environment variable works too, for developers.
"""
import os
from typing import Optional, Tuple

KEY_TARGET = "JARVIS/TTS/fish"
KEY_USER = "api-key"
KEY_ENV = "FISH_API_KEY"


def api_key() -> Tuple[Optional[str], Optional[str]]:
    """(key, source): source is "saved" (secret store), "env:FISH_API_KEY" or None when there is no key."""
    from src.core import credentials
    key = credentials.read(KEY_TARGET)
    if key:
        return key, "saved"
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV], f"env:{KEY_ENV}"
    return None, None


def save_api_key(key: str) -> None:
    from src.core import credentials
    key = (key or "").strip()
    if not key:
        raise ValueError("Empty API key")
    credentials.write(KEY_TARGET, KEY_USER, key)


def forget_api_key() -> None:
    from src.core import credentials
    credentials.delete(KEY_TARGET)
