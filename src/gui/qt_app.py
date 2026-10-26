"""Jarvis desktop application (PySide6; the pages render in Qt WebEngine on Windows and in the system's WebKit on
macOS, src/gui/webengine_view.py and webkit_view.py).

  MainWindow   native frame (dark caption on Windows 11, dark appearance on macOS), hosts web/index.html
  OrbWindow    frameless, translucent, always-on-top floating orb (web/compact.html)
  Tray         QSystemTrayIcon coloured by state (the menu bar on macOS), with Show / Start / Stop / Mute / Quit

What the windows need from the OS (global shortcut, foreground, title bar) is in src/gui/native.py.

All long-running work (microphone, Whisper, Claude, tools, voice) lives on the
pipeline thread owned by AssistantController; this module only renders and
forwards clicks, so the UI thread never blocks on the assistant.
"""
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon, QWidget

from src.app.controller import AssistantController
from src.core import config_store, logging_setup, paths
from src.gui import native
from src.gui.bridge import Bridge, EventHub
from src.platform import current

if sys.platform == "darwin":
    from src.gui.webkit_view import make_view as _make_view      # macOS ships WebKit: no Chromium in Jarvis.app
else:
    from src.gui.webengine_view import make_view as _make_view

WEB = Path(__file__).resolve().parent / "web"
INSTANCE_KEY = "jarvis-assistant-single-instance"

STATE_COLORS = {
    "stopped": "#7a7a7d", "loading": "#749dc4", "idle": "#94bce3", "paused": "#b7b7ba",
    "listening": "#d6ebff", "transcribing": "#b5d9fd", "thinking": "#b5d9fd",
    "executing": "#d6ebff", "approval": "#eef6ff", "speaking": "#eef6ff", "error": "#d4d4d7",
}
STATE_LABELS = {
    "stopped": "Offline", "loading": "Starting up", "idle": "Awaiting command", "paused": "Microphone paused",
    "listening": "Listening", "transcribing": "Transcribing", "thinking": "Thinking",
    "executing": "Executing", "approval": "Awaiting your approval", "speaking": "Speaking", "error": "Error",
}

LOGO_ICO = Path(__file__).resolve().parent / "assets" / "jarvis.ico"   # scripts/make_logo.py


def make_icon(color: str = "#94bce3", size: int = 64) -> QIcon:
    """The Jarvis logo (crescent in the HUD ring); a non-default colour adds a state dot (tray)."""
    if color == "#94bce3":
        return QIcon(str(LOGO_ICO))
    pm = QIcon(str(LOGO_ICO)).pixmap(size, size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    d = size * 3 // 8
    p.setPen(QPen(QColor("#03111e"), max(2, size // 20)))
    p.setBrush(QColor(color))
    p.drawEllipse(QRect(size - d - 1, size - d - 1, d, d))
    p.end()
    return QIcon(pm)


def make_view(parent, bridge: Bridge, page_file: str, transparent: bool = False):
    return _make_view(parent, bridge, WEB / page_file, transparent)


class MainWindow(QMainWindow):
    def __init__(self, app: "JarvisQtApp"):
        super().__init__()
        self.app = app
        self.setWindowTitle(app.name)
        self.setWindowIcon(app.icon)
        self.resize(1180, 780)
        self.setMinimumSize(760, 560)
        self.bridge = Bridge(app, "main")
        self.view = make_view(self, self.bridge, "index.html")
        self.setCentralWidget(self.view)
        native.style_window(self)

    def closeEvent(self, event):
        if self.app.quitting:
            event.accept()
            return
        if config_store.load_config().get("gui", {}).get("close_to_tray", True):
            event.ignore()
            self.app.collapse_to_orb()
        else:
            event.ignore()
            self.app.quit()


class OrbWindow(QWidget):
    """Compact always-available assistant: orb, caption and state, bottom-right of the screen."""

    W, H = 340, 230

    def __init__(self, app: "JarvisQtApp"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle(f"{app.name} orb")
        self.resize(self.W, self.H)
        self.bridge = Bridge(app, "compact")
        self.view = make_view(self, self.bridge, "compact.html", transparent=True)
        self.view.setGeometry(0, 0, self.W, self.H)
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent")
        self._placed = False

    def place(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.W - 12, screen.bottom() - self.H - 12)
        self._placed = True

    def showEvent(self, event):
        if not self._placed:
            self.place()
        super().showEvent(event)


class QuitFilter(QObject):
    """A quit that doesn't come from Jarvis (macOS's Quit menu or ⌘Q, signing out) goes through JarvisQtApp.quit(),
    so the assistant stops in order; ignoring the event tells macOS to wait."""

    def __init__(self, app: "JarvisQtApp"):
        super().__init__()
        self.app = app

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Quit and not self.app.quitting:
            event.ignore()
            QTimer.singleShot(0, self.app.quit)
            return True
        return False


class JarvisQtApp:
    def __init__(self, qapp: QApplication, start_minimized: bool = False):
        self.qapp = qapp
        self.quitting = False
        self._quit_filter = QuitFilter(self)
        qapp.installEventFilter(self._quit_filter)
        self.icon = make_icon()
        qapp.setWindowIcon(self.icon)
        self.name = config_store.assistant_name()       # what the user calls the assistant (Settings › General)
        self.hub = EventHub()
        self.hub.quit_requested.connect(qapp.quit)
        self.hub.quit_later.connect(lambda ms: QTimer.singleShot(ms, self.quit))
        self.controller = AssistantController()
        self.controller.add_listener(self._on_controller_event)
        logging_setup.add_listener(self.hub.emit_log)

        self.main = MainWindow(self)
        self.orb = OrbWindow(self)
        self._build_tray()
        self._register_hotkey()

        full_cfg = config_store.load_config()
        cfg = full_cfg.get("gui", {})
        self._announce_ready = False
        from src.app import setup as first_run
        self.setup_needed = first_run.needed(full_cfg)
        if self.setup_needed:
            self.main.show()                    # the setup wizard; the assistant starts from its last screen
        elif start_minimized or cfg.get("start_minimized", False):
            # Started at sign-in: the floating orb (click it to talk, Ctrl+Shift+J for the window), or tray only.
            if cfg.get("startup_view", "tray") == "orb":
                self.orb.show()
            self._announce_ready = True
        else:
            self.main.show()
        if cfg.get("autostart_pipeline", True) and not self.setup_needed:
            QTimer.singleShot(400, self.controller.start)
        if not self.setup_needed:
            threading.Thread(target=self._check_claude, name="claude-check", daemon=True).start()
        QTimer.singleShot(3000, self._after_update)
        updates_cfg = full_cfg.get("updates", {}) or {}
        if updates_cfg.get("auto_check", True) and updates_cfg.get("url"):
            threading.Thread(target=self._check_updates, args=(updates_cfg["url"],), name="update-check", daemon=True).start()

    def _after_update(self) -> None:
        """Say how the last update went, and offer one that was downloaded earlier but not installed yet."""
        from src.app import updates
        from src.version import __version__
        try:
            result = updates.after_start()
            waiting = updates.staged_version()
        except Exception as e:
            print(f"[UPDATE] Cleanup after an update failed: {e}")
            return
        if result == "updated":
            self.hub.emit_event({"type": "banner", "code": "updated", "message": f"{self.name} was updated to version {__version__}."})
        elif result:
            self.hub.emit_event({"type": "banner", "code": "update_failed",
                                 "message": "The update didn't install: " + result.removeprefix("failed: ")})
        if waiting:
            self._update_ready(waiting)

    def _update_ready(self, version: str) -> None:
        self.hub.emit_event({"type": "update_ready", "version": version})
        self.hub.emit_event({"type": "banner", "code": "update", "message": f"Version {version} is ready to install.",
                             "fix": {"label": "Restart to update", "action": "update_restart"}})

    def _check_updates(self, url: str) -> None:
        """Once per start (the app is restarted at least daily for most people): get a newer version ready, or
        offer its download when Jarvis can't update itself (run from source, or from a read-only folder)."""
        from src.app import updates
        result = updates.check(url)
        if not result.get("available") or updates.staged_version() == result.get("version"):
            return
        if updates.updater_available():
            self.prepare_update(result)
        else:
            self.hub.emit_event({"type": "banner", "code": "update", "message": f"Version {result['version']} is available.",
                                 "fix": {"label": "Get it", "action": "open_url", "arg": result.get("notes") or result["download"]["url"]}})

    def prepare_update(self, result: dict = None) -> None:
        """Download, check and unpack a newer version (worker thread), reporting progress as update_* events."""
        from src.app import updates
        result = result or updates.check(config_store.load_config().get("updates", {}).get("url", ""))
        if not result.get("available"):
            self.hub.emit_event(dict(result, type="update_check", can_install=updates.updater_available()))
            return
        last = [0]

        def progress(done: int, total: int) -> None:
            percent = int(done * 100 / total) if total else 0
            if percent != last[0]:
                last[0] = percent
                self.hub.emit_event({"type": "update_progress", "version": result["version"], "percent": percent})

        try:
            updates.prepare(result["download"], progress)
        except Exception as e:
            self.hub.emit_event({"type": "update_check", "available": True, "version": result["version"],
                                 "error": f"Downloading version {result['version']} failed: {e}"})
            return
        self._update_ready(result["version"])

    def _check_claude(self) -> None:
        """With Claude as the brain: say at once when Claude Code is missing or signed out (a banner)."""
        from src.agent.providers import role
        from src.app import errors, setup
        try:
            if role(config_store.load_config(), "main")[2]["kind"] != "claude_code":
                return
        except Exception:
            return
        status = setup.claude_status()
        if not status.get("installed"):
            info = errors.entry("claude_missing", "Claude needs Claude Code, Anthropic's app, which isn't installed.",
                                "Get Claude ready", "claude_install")
        elif not status.get("signed_in") and not status.get("error"):
            info = errors.entry("signed_out", "You're signed out of Claude.", "Sign in", "claude_login")
        else:
            return
        self.hub.emit_event(dict(info, type="banner"))

    # ------------------------------------------------------------ events

    def _on_controller_event(self, ev: dict) -> None:
        # Pipeline thread -> Qt signal (queued to the UI thread).
        # Widgets are only touched from _poll_ui on the UI thread.
        if ev.get("type") == "error":
            from src.app.errors import explain
            info = explain(str(ev.get("stage", "")), str(ev.get("detail", "")), self.name)
            if info:
                ev = dict(ev, **info)           # the window shows a banner with the fix button
        self.hub.emit_event(ev)
        t = ev.get("type")
        if t in ("phase", "mute", "stopped"):
            self._tray_dirty = True
        elif t == "error":
            self._pending_notice = (f"{self.name} · {ev.get('stage', 'error')}", str(ev.get("detail", ""))[:240])
        elif t == "approval_request":
            self._pending_approval = ev.get("title", "")
        elif t == "ready":
            self._pending_ready = ev

    def _poll_ui(self):
        """Runs on the UI thread: apply tray updates and notifications requested by worker threads."""
        if getattr(self, "_tray_dirty", False):
            self._tray_dirty = False
            self.refresh_tray()
        notice = getattr(self, "_pending_notice", None)
        if notice:
            self._pending_notice = None
            if not self.main.isVisible():
                self.tray.showMessage(notice[0], notice[1], QSystemTrayIcon.Warning, 6000)
        ready = getattr(self, "_pending_ready", None)
        if ready is not None:
            self._pending_ready = None
            if self._announce_ready and not self.main.isVisible():
                self._announce_ready = False
                hint = f"Click the {'menu bar' if sys.platform == 'darwin' else 'tray'} icon or press {native.HOTKEY_LABEL} to open it."
                self.tray.showMessage(f"{self.name} is running", hint, QSystemTrayIcon.Information, 5000)
        approval = getattr(self, "_pending_approval", None)
        if approval is not None:
            self._pending_approval = None
            if not self.main.isVisible():
                # Permission prompts must be seen: bring the main window up.
                self.show_main(greet=False)

    # ------------------------------------------------------------ tray

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        self.act_show = QAction(f"Open {self.name}", menu, triggered=self.show_main)
        self.act_orb = QAction("Show floating orb", menu, triggered=self.toggle_orb)
        self.act_start = QAction("Start assistant", menu, triggered=self.controller.start)
        self.act_stop = QAction("Stop assistant", menu,
                                triggered=lambda: threading.Thread(target=self.controller.stop, daemon=True).start())
        self.act_mute = QAction("Mute microphone", menu, checkable=True,
                                triggered=lambda checked: self.controller.set_muted(checked))
        self.act_log = QAction("Open log file", menu, triggered=lambda: current().open_path(str(logging_setup.LOG_FILE)))
        self.act_quit = QAction("Quit", menu, triggered=self.quit)
        for a in (self.act_show, self.act_orb, None, self.act_start, self.act_stop, self.act_mute, None, self.act_log, None, self.act_quit):
            menu.addSeparator() if a is None else menu.addAction(a)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.setToolTip(self.name)
        self.tray.show()
        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._poll_ui)
        self._ui_timer.start(150)
        self.refresh_tray()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.main.isVisible() and not self.main.isMinimized():
                self.collapse_to_orb()
            else:
                self.show_main()

    def refresh_tray(self):
        c = self.controller
        phase = c.phase
        if phase == "idle" and c.muted:
            phase = "paused"
        self.tray.setIcon(make_icon(STATE_COLORS.get(phase, "#94bce3")))
        self.tray.setToolTip(f"{self.name} — {STATE_LABELS.get(phase, phase)}")
        self.act_start.setEnabled(not c.running)
        self.act_stop.setEnabled(c.running)
        self.act_mute.setChecked(c.muted)
        self.act_orb.setText("Hide floating orb" if self.orb.isVisible() else "Show floating orb")

    # ------------------------------------------------------------ windows

    def show_main(self, force: bool = False, reason: str = "user", greet: bool = True):
        """Open the main window. Opening it from the orb, tray or Ctrl+Shift+J (it was hidden or
        minimized) gets a new spoken greeting."""
        opening = not self.main.isVisible() or self.main.isMinimized()
        self.main.showNormal() if self.main.isMinimized() else self.main.show()
        self.main.raise_()
        self.main.activateWindow()
        if force:
            native.force_foreground(self.main)
        self.orb.hide()
        self.hub.emit_event({"type": "window", "state": "expanded", "reason": reason})
        if greet and opening:
            self.controller.greet()
        self.refresh_tray()

    def collapse_to_orb(self):
        self.main.hide()
        self.orb.show()
        self.hub.emit_event({"type": "window", "state": "collapsed"})
        self.refresh_tray()

    def toggle_orb(self):
        self.orb.setVisible(not self.orb.isVisible())
        self.refresh_tray()

    def toggle_main(self):
        if self.main.isVisible() and self.main.isActiveWindow():
            self.collapse_to_orb()
        else:
            self.show_main()

    def on_hotkey(self):
        """Ctrl+Shift+J from any app: open Jarvis in front (or collapse it to the orb if it is already in front)."""
        if self.main.isVisible() and self.main.isActiveWindow():
            print("[GUI] Hotkey: collapsing to the orb")
            self.collapse_to_orb()
        else:
            print("[GUI] Hotkey: opening the main window")
            self.show_main(force=True, reason="hotkey")

    def open_uninstall(self) -> None:
        """Settings › About, where Uninstall is (asked for by Settings › Apps)."""
        self.show_main(force=True, greet=False)
        QTimer.singleShot(600, lambda: self.hub.emit_event({"type": "open_settings", "section": "about"}))

    def toggle_maximized(self):
        self.main.showNormal() if self.main.isMaximized() else self.main.showMaximized()

    def start_compact_move(self):
        handle = self.orb.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def save_screenshots(self, target: Path, view: str = "") -> None:
        """Render the app's own windows to PNG (diagnostics; works while covered by other windows)."""
        target.mkdir(parents=True, exist_ok=True)
        if view:
            self.main.view.page().runJavaScript(
                f"document.querySelector('.tab[data-view=\"{view}\"]') && document.querySelector('.tab[data-view=\"{view}\"]').click()")

        def grab():
            name = view or "assistant"
            self.main.view.grab().save(str(target / f"main-{name}.png"))
            self.orb.view.grab().save(str(target / "orb.png"))
            print(f"[GUI] Screenshots saved to {target}")

        QTimer.singleShot(900 if view else 50, grab)

    def apply_gui_settings(self):
        cfg = config_store.load_config()
        self.apply_name(config_store.assistant_name(cfg))
        self.hub.emit_event({"type": "settings_changed", "config": cfg.get("gui", {}), "assistant_name": self.name})

    def apply_name(self, name: str) -> None:
        """A rename (wizard or Settings) takes effect at once in titles, the tray and its menu."""
        self.name = name
        self.main.setWindowTitle(name)
        self.orb.setWindowTitle(f"{name} orb")
        self.act_show.setText(f"Open {name}")
        self.refresh_tray()

    def _register_hotkey(self):
        """Ctrl+Shift+J (⌃⇧J on a Mac) in any app toggles the main window / orb."""
        self.hotkey = native.GlobalHotkey(self.qapp, lambda: QTimer.singleShot(0, self.on_hotkey))
        self.hotkey.register()

    # ------------------------------------------------------------ quit

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        print("[GUI] Quit requested — stopping the assistant")
        self.main.hide()
        self.orb.hide()
        self.tray.setToolTip(f"{self.name} — shutting down")
        self.hotkey.unregister()

        def _stop():
            self.controller.shutdown()
            self.hub.quit_requested.emit()   # queued to the UI thread

        threading.Thread(target=_stop, name="jarvis-shutdown", daemon=True).start()
        # Hard limit: never hang on exit.
        QTimer.singleShot(25000, self.qapp.quit)


def _another_instance_running(message: bytes = b"show") -> bool:
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(500):
        sock.write(message)
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True
    return False


def run(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging_setup.setup_logging(capture_std=True)
    print(f"[GUI] Starting Jarvis desktop (Python {sys.version.split()[0]}, pid {__import__('os').getpid()})")
    native.set_app_id()
    from src.core import winproc
    winproc.extend_path()
    print(f"[GUI] Data folder: {paths.home()}")
    config_store.migrate_user_config()

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(argv)
    qapp.setApplicationName("Jarvis")
    qapp.setQuitOnLastWindowClosed(False)

    # Dev/diagnostics: `jarvis_gui.pyw --screenshot <dir> [view]` asks the running
    # instance to save PNGs of its own windows (main + orb) without touching the screen.
    message = b"show"
    if "--screenshot" in argv:
        i = argv.index("--screenshot")
        target = argv[i + 1] if i + 1 < len(argv) else str(paths.cache_dir() / "screens")
        view = argv[i + 2] if i + 2 < len(argv) else ""
        message = f"screenshot|{target}|{view}".encode("utf-8")
    elif "--quit" in argv:
        message = b"quit"
    elif "--uninstall" in argv:                   # Settings › Apps › Uninstall (src/app/install.py registers it)
        message = b"uninstall"
    elif "--eval" in argv:
        # Dev only (honoured when the running instance was started with JARVIS_DEV=1):
        # run JavaScript in the main page, e.g. to drive the real UI in end-to-end checks.
        message = ("js|" + argv[argv.index("--eval") + 1]).encode("utf-8")
    if _another_instance_running(message):
        print("[GUI] Another Jarvis is already running — forwarded: " + message.decode("utf-8"))
        return 0
    if message not in (b"show", b"uninstall"):
        print("[GUI] --screenshot needs a running Jarvis")
        return 1
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE_KEY)
    server.listen(INSTANCE_KEY)

    app = JarvisQtApp(qapp, start_minimized="--minimized" in argv)
    if message == b"uninstall":
        QTimer.singleShot(2500, app.open_uninstall)

    def _handle(data: bytes):
        text = bytes(data).decode("utf-8", errors="replace")
        if text.startswith("screenshot|"):
            _, target, view = (text.split("|") + ["", ""])[:3]
            app.save_screenshots(Path(target), view)
        elif text == "quit":
            app.quit()
        elif text == "uninstall":
            app.open_uninstall()
        elif text.startswith("js|"):
            if os.environ.get("JARVIS_DEV") == "1":
                app.main.view.page().runJavaScript(text[3:])
            else:
                print("[GUI] Ignored --eval: start Jarvis with JARVIS_DEV=1 to allow it")
        else:
            app.show_main()

    def _on_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: _handle(conn.readAll()))

    server.newConnection.connect(_on_connection)
    code = qapp.exec()
    print(f"[GUI] Exited ({code})")
    return code
