"""OS-specific services (folders, secrets, start at login, files and links, permissions, media keys) behind one
interface.

current() returns the implementation for this OS: Windows or macOS. Elsewhere the generic base still gives the right
folders, and the OS-specific services raise NotImplementedError.
"""
import sys
from functools import lru_cache

from src.platform.base import APP_NAME, PERMISSIONS, Platform

__all__ = ["APP_NAME", "PERMISSIONS", "Platform", "current"]


@lru_cache(maxsize=1)
def current() -> Platform:
    if sys.platform == "win32":
        from src.platform.windows import WindowsPlatform
        return WindowsPlatform()
    if sys.platform == "darwin":
        from src.platform.macos import MacPlatform
        return MacPlatform()
    return Platform()
