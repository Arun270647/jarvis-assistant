"""What the desktop window needs from the OS: the global shortcut, bringing a window to the front, the title bar's
colour and the app's identity in the taskbar.

Windows: RegisterHotKey (its WM_HOTKEY arrives through a Qt native event filter), the ALT + AttachThreadInput trick
for the foreground, DWM for the dark caption. macOS: Carbon's RegisterEventHotKey through ctypes (no permission
needed, unlike a key-event monitor), NSApp.activate for the foreground and the dark Aqua appearance. Both use the
same keys, Control+Shift+J.
"""
import ctypes
import sys
from typing import Callable, Optional

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

APP_ID = "Jarvis.Assistant.Desktop"
HOTKEY_LABEL = "⌃⇧J" if IS_MAC else "Ctrl+Shift+J"     # shown in the UI and the tray's messages

HOTKEY_ID = 0x4A41  # 'JA'
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312

CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"
KVK_ANSI_J = 0x26
CARBON_SHIFT, CARBON_CONTROL = 1 << 9, 1 << 12
KEVENT_HOTKEY_PRESSED = 5


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def set_app_id() -> None:
    """Windows groups the taskbar button and notifications under Jarvis rather than Python."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


def style_window(widget) -> None:
    """A dark title bar matching the app (Windows 11's caption colour; macOS's dark appearance for the whole app)."""
    if IS_WINDOWS:
        try:
            hwnd = int(widget.winId())
            value = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / pre-20H1)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            caption = ctypes.c_int(0x003D2D1D)  # COLORREF 0x00BBGGRR of #1d2d3d
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        except Exception:
            pass
    elif IS_MAC:
        try:
            from AppKit import NSApplication, NSAppearance
            NSApplication.sharedApplication().setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        except Exception:
            pass


def force_foreground(widget) -> None:
    """Bring a window to the front even when Jarvis is a background (tray or menu bar) process.

    Windows' focus-stealing rules normally only flash the taskbar button for SetForegroundWindow from a background
    process; a synthetic ALT key press plus AttachThreadInput is the documented-in-practice way around it. macOS
    lets the app activate itself when the user asked for it (the shortcut, the menu bar icon).
    """
    try:
        if IS_MAC:
            from AppKit import NSApplication
            app = NSApplication.sharedApplication()
            if hasattr(app, "activate"):
                app.activate()                              # macOS 14 and later
            else:
                app.activateIgnoringOtherApps_(True)
            return
        if not IS_WINDOWS:
            return
        user32 = ctypes.windll.user32
        hwnd = int(widget.winId())
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.keybd_event(0x12, 0, 0, 0)                   # ALT down
        user32.keybd_event(0x12, 0, 2, 0)                   # ALT up
        if fg_thread and fg_thread != me:
            user32.AttachThreadInput(me, fg_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if fg_thread and fg_thread != me:
            user32.AttachThreadInput(me, fg_thread, False)
    except Exception as e:
        print(f"[GUI] Could not bring the window to the front: {e}")


class GlobalHotkey:
    """Control+Shift+J from any app calls `callback` on the UI thread's event loop."""

    def __init__(self, qapp, callback: Callable[[], None]):
        self.qapp = qapp
        self.callback = callback
        self._filter = None
        self._mac: Optional[dict] = None

    def register(self) -> bool:
        try:
            ok = self._register_windows() if IS_WINDOWS else self._register_mac() if IS_MAC else False
        except Exception as e:
            print(f"[GUI] Global hotkey failed: {e}")
            return False
        print(f"[GUI] Global hotkey {HOTKEY_LABEL} " + ("registered" if ok else "unavailable (in use by another app)"))
        return ok

    def unregister(self) -> None:
        try:
            if IS_WINDOWS:
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            elif self._mac and self._mac.get("ref"):
                self._mac["carbon"].UnregisterEventHotKey(self._mac["ref"])
                self._mac["ref"] = None
        except Exception:
            pass

    # ------------------------------------------------------------ Windows

    def _register_windows(self) -> bool:
        from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray

        callback = self.callback

        class HotkeyFilter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):
                if event_type in (b"windows_generic_MSG", "windows_generic_MSG", QByteArray(b"windows_generic_MSG")):
                    try:
                        from ctypes import wintypes
                        msg = wintypes.MSG.from_address(int(message))
                        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                            callback()
                            return True, 0
                    except Exception:
                        pass
                return False, 0

        self._filter = HotkeyFilter()
        self.qapp.installNativeEventFilter(self._filter)
        return bool(ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT,
                                                        ord("J")))

    # ------------------------------------------------------------ macOS (Carbon)

    def _register_mac(self) -> bool:
        """Carbon delivers the key press on the main thread's run loop, which Qt's Cocoa event loop runs."""
        carbon = ctypes.CDLL(CARBON)

        class EventHotKeyID(ctypes.Structure):
            _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

        class EventTypeSpec(ctypes.Structure):
            _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

        handler_type = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, handler_type, ctypes.c_size_t,
                                               ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

        def on_key(_call, _event, _data):
            try:
                self.callback()
            except Exception as e:
                print(f"[GUI] Hotkey handler failed: {e}")
            return 0

        handler = handler_type(on_key)          # kept in self._mac: Carbon calls it for as long as Jarvis runs
        target = carbon.GetApplicationEventTarget()
        spec = EventTypeSpec(_fourcc("keyb"), KEVENT_HOTKEY_PRESSED)
        handler_ref, hotkey_ref = ctypes.c_void_p(), ctypes.c_void_p()
        if carbon.InstallEventHandler(target, handler, 1, ctypes.byref(spec), None, ctypes.byref(handler_ref)) != 0:
            return False
        status = carbon.RegisterEventHotKey(KVK_ANSI_J, CARBON_CONTROL | CARBON_SHIFT,
                                            EventHotKeyID(_fourcc("JRVS"), 1), target, 0, ctypes.byref(hotkey_ref))
        self._mac = {"carbon": carbon, "handler": handler, "handler_ref": handler_ref,
                     "ref": hotkey_ref if status == 0 else None}
        return status == 0
