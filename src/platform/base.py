"""The services Jarvis needs from the operating system, as one interface.

Each OS has one implementation (src/platform/windows.py; macOS follows, see the planning notes).
Folders come from platformdirs, so they already follow each OS's conventions; secrets and
start-at-login are OS-specific and implemented by the subclass.
"""
from pathlib import Path
from typing import Optional

import platformdirs

APP_NAME = "Jarvis"


class Platform:
    name = "generic"

    # ------------------------------------------------------------ folders

    def data_dir(self) -> Path:
        """Small per-user files that may roam: settings, profile, database, logs."""
        return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=True))

    def local_dir(self) -> Path:
        """Per-user files that stay on this machine (large downloads)."""
        return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=False))

    def models_dir(self) -> Path:
        return self.local_dir() / "models"

    def cache_dir(self) -> Path:
        return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))

    # ------------------------------------------------------------ secrets

    def read_secret(self, target: str) -> Optional[str]:
        raise NotImplementedError(f"secrets are not supported on {self.name} yet")

    def write_secret(self, target: str, username: str, secret: str) -> None:
        raise NotImplementedError(f"secrets are not supported on {self.name} yet")

    def delete_secret(self, target: str) -> None:
        raise NotImplementedError(f"secrets are not supported on {self.name} yet")

    # ------------------------------------------------------------ start at login

    def autostart_enabled(self) -> bool:
        return False

    def set_autostart(self, enabled: bool, program: str, arguments: str = "", working_dir: str = "",
                      description: str = "", icon: str = "") -> bool:
        """Start (or stop starting) `program arguments` when the user signs in. Returns the new state."""
        raise NotImplementedError(f"start at login is not supported on {self.name} yet")
