"""Windows: Credential Manager for secrets, a Startup-folder shortcut (made through COM) for start at login.

Folders: %APPDATA%\\Jarvis for settings and data, %LOCALAPPDATA%\\Jarvis\\models and \\cache for large
local files. Shortcuts are created with WScript.Shell over COM (pywin32), in-process, because shelling
out to powershell.exe flashes a console window under pythonw.
"""
import os
from pathlib import Path
from typing import Optional

from src.platform.base import APP_NAME, Platform

SHORTCUT_NAME = f"{APP_NAME}.lnk"


class WindowsPlatform(Platform):
    name = "windows"

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
