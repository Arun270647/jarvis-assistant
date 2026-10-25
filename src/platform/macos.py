"""macOS: the Keychain for secrets (through keyring), a LaunchAgent for start at login, `open` for files and links,
and System Settings' Privacy & Security pages for the permissions macOS asks the user for.

Folders (platformdirs): ~/Library/Application Support/Jarvis for settings, data and models, ~/Library/Caches/Jarvis
for the cache. PyObjC and keyring are imported inside the methods that need them, so this module loads anywhere
(the tests run it on Windows with fakes).
"""
import plistlib
import shlex
from pathlib import Path
from typing import Optional

from src.platform.base import APP_NAME, Platform

BUNDLE_ID = "jarvis.assistant.desktop"     # Jarvis.app's CFBundleIdentifier (packaging/build_macos.py)
KEYCHAIN_ACCOUNT = APP_NAME                # a secret's target is its Keychain service; the account is always this
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?"
SETTINGS_PANES = {"microphone": "Privacy_Microphone", "accessibility": "Privacy_Accessibility",
                  "screen": "Privacy_ScreenCapture", "automation": "Privacy_Automation"}
AV_STATUS = {0: "not_determined", 1: "denied", 2: "denied", 3: "granted"}     # AVAuthorizationStatus

# Play/pause and track keys are system-defined key events (NX_KEYTYPE_*), which macOS lets an app post once it has
# the Accessibility permission. The volume goes through AppleScript's standard additions, which need no permission.
MEDIA_KEYS = {"play_pause": 16, "next_track": 17, "prev_track": 18}
VOLUME_SCRIPTS = {
    "volume_up": "set volume output volume ((output volume of (get volume settings)) + 6)",
    "volume_down": "set volume output volume ((output volume of (get volume settings)) - 6)",
    "volume_mute": "set volume output muted (not (output muted of (get volume settings)))",
}
NS_SYSTEM_DEFINED, AUX_CONTROL_BUTTONS = 14, 8


class MacPlatform(Platform):
    name = "macos"
    label = "macOS"
    computer = "Mac"
    machine = "Mac"
    terminal = "Terminal window"

    # ------------------------------------------------------------ secrets (Keychain)

    def read_secret(self, target: str) -> Optional[str]:
        try:
            import keyring
            return keyring.get_password(target, KEYCHAIN_ACCOUNT)
        except Exception:
            return None

    def write_secret(self, target: str, username: str, secret: str) -> None:
        import keyring
        keyring.set_password(target, KEYCHAIN_ACCOUNT, secret)

    def delete_secret(self, target: str) -> None:
        try:
            import keyring
            keyring.delete_password(target, KEYCHAIN_ACCOUNT)
        except Exception:
            pass

    # ------------------------------------------------------------ start at login (LaunchAgent)

    @staticmethod
    def launch_agent() -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"

    def autostart_enabled(self) -> bool:
        return self.launch_agent().exists()

    def set_autostart(self, enabled: bool, program: str, arguments: str = "", working_dir: str = "",
                      description: str = "", icon: str = "") -> bool:
        """launchd starts the agent at the next sign-in (System Settings › General › Login Items lists it)."""
        path = self.launch_agent()
        if enabled:
            job = {"Label": BUNDLE_ID, "ProgramArguments": [program] + shlex.split(arguments),
                   "RunAtLoad": True, "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua"}
            if working_dir:
                job["WorkingDirectory"] = working_dir
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(job))
        elif path.exists():
            path.unlink()
        return path.exists()

    # ------------------------------------------------------------ files, links, settings

    def open_path(self, target: str) -> None:
        from src.core import winproc
        winproc.popen_hidden(["/usr/bin/open", str(target)])

    def reveal(self, path: str) -> None:
        from src.core import winproc
        winproc.popen_hidden(["/usr/bin/open", "-R", str(path)])

    def privacy_settings(self, kind: str) -> bool:
        pane = SETTINGS_PANES.get(kind)
        if not pane:
            return False
        self.open_path(SETTINGS_URL + pane)
        return True

    # ------------------------------------------------------------ permissions

    def permission(self, kind: str) -> str:
        """Accessibility and Screen Recording only say yes or no; the microphone also says "never asked"."""
        try:
            if kind == "microphone":
                import AVFoundation
                status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
                return AV_STATUS.get(int(status), "unknown")
            if kind == "accessibility":
                import ApplicationServices
                return "granted" if ApplicationServices.AXIsProcessTrusted() else "denied"
            if kind == "screen":
                import Quartz
                return "granted" if Quartz.CGPreflightScreenCaptureAccess() else "denied"
        except Exception as e:
            print(f"[PLATFORM] Could not read the {kind} permission: {e}")
        return "unknown"

    def request_permission(self, kind: str) -> str:
        """macOS shows its prompt only the first time; after a "Don't Allow" only System Settings can change it.
        Screen Recording takes effect after Jarvis is reopened."""
        try:
            if kind == "microphone":
                import AVFoundation
                AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVFoundation.AVMediaTypeAudio, lambda granted: None)
            elif kind == "accessibility":
                import ApplicationServices
                ApplicationServices.AXIsProcessTrustedWithOptions(
                    {ApplicationServices.kAXTrustedCheckOptionPrompt: True})
            elif kind == "screen":
                import Quartz
                Quartz.CGRequestScreenCaptureAccess()
        except Exception as e:
            print(f"[PLATFORM] Could not ask for the {kind} permission: {e}")
        return self.permission(kind)

    # ------------------------------------------------------------ media keys

    def send_media_key(self, key: str) -> None:
        from src.core import winproc
        if key in VOLUME_SCRIPTS:
            r = winproc.run_hidden(["/usr/bin/osascript", "-e", VOLUME_SCRIPTS[key]], timeout=10,
                                   capture_output=True, text=True)
            if r.returncode != 0:
                raise OSError((r.stderr or "").strip() or "osascript failed")
            return
        code = MEDIA_KEYS.get(key)
        if code is None:
            raise ValueError(f"Unknown media key: {key}")
        import Quartz
        from AppKit import NSEvent
        if not Quartz.CGPreflightPostEventAccess():
            Quartz.CGRequestPostEventAccess()
            raise PermissionError("macOS doesn't let Jarvis press media keys yet: turn Jarvis on in System Settings "
                                  "› Privacy & Security › Accessibility, then try again.")
        for down in (True, False):
            state = 0xA if down else 0xB
            event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NS_SYSTEM_DEFINED, (0, 0), state << 8, 0, 0, None, AUX_CONTROL_BUTTONS, (code << 16) | (state << 8), -1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())
