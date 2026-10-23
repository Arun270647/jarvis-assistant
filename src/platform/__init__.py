"""OS-specific services (folders, secrets, start at login) behind one interface.

current() returns the implementation for this OS. Windows is the only supported OS so far; elsewhere
the generic base still gives the right folders, and the OS-specific services raise NotImplementedError.
The remaining Windows-only modules (desktop control, terminals, hotkey, ...) move behind this layer
with the macOS port.
"""
import sys
from functools import lru_cache

from src.platform.base import APP_NAME, Platform

__all__ = ["APP_NAME", "Platform", "current"]


@lru_cache(maxsize=1)
def current() -> Platform:
    if sys.platform == "win32":
        from src.platform.windows import WindowsPlatform
        return WindowsPlatform()
    return Platform()
