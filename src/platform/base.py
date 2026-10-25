"""The services Jarvis needs from the operating system, as one interface.

Each OS has one implementation (src/platform/windows.py, src/platform/macos.py). Folders come from platformdirs, so
they already follow each OS's conventions; the rest is OS-specific and implemented by the subclass. What the desktop
window needs from the OS (global shortcut, bringing a window to the front) is in src/gui/native.py, desktop control
in src/tools/desktop_win.py and desktop_mac.py.
"""
from pathlib import Path
from typing import Optional

import platformdirs

APP_NAME = "Jarvis"

# Permissions a user grants in the OS's settings (macOS asks for each; Windows only has the microphone switch).
PERMISSIONS = ("microphone", "accessibility", "screen", "automation")


class Platform:
    name = "generic"
    label = "this computer"         # the OS's name in messages ("Windows", "macOS")
    computer = "computer"           # what the user calls the machine ("PC", "Mac")
    machine = "computer"            # the machine with its OS, for the assistant's prompt ("Windows PC", "Mac")
    terminal = "terminal"           # the terminal windows Jarvis opens ("Command Prompt", "Terminal window")

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

    # ------------------------------------------------------------ files, links, settings

    def open_path(self, target: str) -> None:
        """Open a file, folder or link (https:, mailto:) with its default app."""
        raise NotImplementedError(f"opening files is not supported on {self.name} yet")

    def reveal(self, path: str) -> None:
        """Show a file selected in the file manager."""
        self.open_path(str(Path(path).parent))

    def privacy_settings(self, kind: str) -> bool:
        """Open the OS's settings page for a permission (PERMISSIONS). False when there is no such page."""
        return False

    # ------------------------------------------------------------ permissions

    def permission(self, kind: str) -> str:
        """'granted', 'denied', 'not_determined' (never asked) or 'unknown' (this OS doesn't say) for a permission."""
        return "unknown"

    def request_permission(self, kind: str) -> str:
        """Ask the OS for a permission (it shows its own prompt, at most once). Returns the state afterwards."""
        return self.permission(kind)

    # ------------------------------------------------------------ media keys

    def send_media_key(self, key: str) -> None:
        """play_pause, stop, next_track, prev_track, volume_up, volume_down, volume_mute."""
        raise NotImplementedError(f"media keys are not supported on {self.name} yet")
