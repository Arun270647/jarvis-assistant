"""Windows: Credential Manager for secrets, a Startup-folder shortcut (made through COM) for start at login,
ShellExecute for files and links, virtual media keys.

Folders: %APPDATA%\\Jarvis for settings and data, %LOCALAPPDATA%\\Jarvis\\models and \\cache for large
local files. Shortcuts are created with WScript.Shell over COM (pywin32), in-process, because shelling
out to powershell.exe flashes a console window under pythonw.
"""
import os
from pathlib import Path
from typing import Optional

from src.platform.base import APP_NAME, Platform

SHORTCUT_NAME = f"{APP_NAME}.lnk"


# Virtual-key codes of the media keys (keybd_event)
MEDIA_KEYS = {"play_pause": 0xB3, "stop": 0xB2, "next_track": 0xB0, "prev_track": 0xB1,
              "volume_up": 0xAF, "volume_down": 0xAE, "volume_mute": 0xAD}


class WindowsPlatform(Platform):
    name = "windows"
    label = "Windows"
    computer = "PC"
    machine = "Windows PC"
    terminal = "Command Prompt"

    def cache_dir(self) -> Path:
        return self.local_dir() / "cache"

    # ------------------------------------------------------------ secrets (Credential Manager)

    def read_secret(self, target: str) -> Optional[str]:
        try:
            import win32cred
            cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)
            blob = cred["CredentialBlob"]
            return blob.decode("utf-16-le") if isinstance(blob, bytes) else blob
        except Exception:
            return None

    def write_secret(self, target: str, username: str, secret: str) -> None:
        import win32cred
        win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": target,
                             "UserName": username, "CredentialBlob": secret,
                             "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)

    def delete_secret(self, target: str) -> None:
        try:
            import win32cred
            win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
        except Exception:
            pass

    # ------------------------------------------------------------ start at login (Startup folder)

    @staticmethod
    def startup_dir() -> Path:
        return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    def startup_shortcut(self) -> Path:
        return self.startup_dir() / SHORTCUT_NAME

    @staticmethod
    def desktop_dir() -> Path:
        """The real Desktop folder (OneDrive can redirect it away from %USERPROFILE%\\Desktop)."""
        try:
            from win32com.shell import shell, shellcon
            return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
        except Exception:
            return Path(os.environ.get("USERPROFILE", "")) / "Desktop"

    @staticmethod
    def create_shortcut(path: Path, target: str, arguments: str = "", working_dir: str = "",
                        description: str = "", icon: str = "") -> Path:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shell = win32com.client.Dispatch("WScript.Shell")
            sc = shell.CreateShortcut(str(path))
            sc.TargetPath = target
            sc.Arguments = arguments
            sc.WorkingDirectory = working_dir
            sc.Description = description
            if icon:
                sc.IconLocation = icon
            sc.Save()
            del sc, shell          # release COM objects before CoUninitialize
        finally:
            pythoncom.CoUninitialize()
        return path

    def autostart_enabled(self) -> bool:
        return self.startup_shortcut().exists()

    def set_autostart(self, enabled: bool, program: str, arguments: str = "", working_dir: str = "",
                      description: str = "", icon: str = "") -> bool:
        path = self.startup_shortcut()
        if enabled:
            self.create_shortcut(path, program, arguments, working_dir, description, icon)
        elif path.exists():
            path.unlink()
        return path.exists()

    # ------------------------------------------------------------ files, links, settings

    def open_path(self, target: str) -> None:
        os.startfile(target)            # ShellExecute: the default app, no console

    def reveal(self, path: str) -> None:
        from src.core import winproc
        winproc.popen_hidden(["explorer", "/select,", str(path)])

    def privacy_settings(self, kind: str) -> bool:
        if kind != "microphone":
            return False
        os.startfile("ms-settings:privacy-microphone")
        return True

    # ------------------------------------------------------------ media keys

    def send_media_key(self, key: str) -> None:
        import ctypes
        vk = MEDIA_KEYS.get(key)
        if vk is None:
            raise ValueError(f"Unknown media key: {key}")
        KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
