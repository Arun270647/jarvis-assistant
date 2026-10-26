"""The window's web view on macOS: the system's WebKit (WKWebView, through PyObjC) inside a Qt widget, carrying the
same QWebChannel bridge that Qt WebEngine carries on Windows (src/gui/webengine_view.py). macOS ships WebKit, so
Jarvis.app doesn't bundle Chromium: Qt WebEngine and the Qt parts it needs are about 290 MB.

- The bridge: a user script defines qt.webChannelTransport. Its send() posts to a WKScriptMessageHandler, which
  hands the message to WebKitTransport (a QWebChannelAbstractTransport); QWebChannel answers through sendMessage(),
  which calls transport.onmessage in the page with evaluateJavaScript. qwebchannel.js comes from Qt's resources and
  is injected the same way, because the pages' qrc:// script tag only works in Qt WebEngine.
- Console warnings, errors and uncaught exceptions go to the log, as WebPage does on Windows.
- Only the app's own pages load; anything else is refused (links open through the bridge's openUrl). If WebKit's
  page process dies, the page reloads.
- A Mac text field takes ⌘C, ⌘V and the rest from the Edit menu (WebKit has no shortcuts of its own for them) and
  Qt adds none, so the first view adds one. The right-click menu shows only on text fields and selected text (the
  Windows window has none). The setup wizard's voice samples play without a click, as they do in Qt WebEngine.

PyObjC is imported inside the functions that need it, so the scripts and the transport are testable on any OS.
"""
import ctypes
import json
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, Qt, QUrl
from PySide6.QtGui import QWindow
from PySide6.QtWebChannel import QWebChannel, QWebChannelAbstractTransport
from PySide6.QtWidgets import QVBoxLayout, QWidget

HANDLER = "jarvis"                  # window.webkit.messageHandlers.jarvis: QWebChannel messages
LOG_HANDLER = "jarvisLog"           # window.webkit.messageHandlers.jarvisLog: console output for the log
BACKGROUND = (0x1d / 255, 0x2d / 255, 0x3d / 255)     # the app's background, behind the page while it loads
# The Edit menu: title, the action sent to the focused view, key with ⌘ (and ⇧ when True). None: a separator.
EDIT_MENU = [("Undo", "undo:", "z", False), ("Redo", "redo:", "z", True), None, ("Cut", "cut:", "x", False),
             ("Copy", "copy:", "c", False), ("Paste", "paste:", "v", False), ("Select All", "selectAll:", "a", False)]
_CLASSES = {}


def transport_script() -> str:
    """Defines qt.webChannelTransport, as Qt WebEngine does, on top of WebKit's message handler."""
    return ("window.qt = window.qt || {};\n"
            "window.qt.webChannelTransport = {\n"
            f"  send: function (message) {{ window.webkit.messageHandlers.{HANDLER}.postMessage(message); }},\n"
            "  onmessage: null\n"
            "};\n")


def console_script() -> str:
    """console.warn/error and uncaught exceptions also go to the log handler."""
    return ("(function () {\n"
            f"  var post = function (level, text) {{ try {{ window.webkit.messageHandlers.{LOG_HANDLER}"
            ".postMessage(level + ' ' + text); } catch (e) {} };\n"
            "  ['warn', 'error'].forEach(function (level) {\n"
            "    var original = console[level];\n"
            "    console[level] = function () {\n"
            "      post(level.toUpperCase(), Array.prototype.join.call(arguments, ' '));\n"
            "      original.apply(console, arguments);\n"
            "    };\n"
            "  });\n"
            "  window.addEventListener('error', function (e) {\n"
            "    post('ERROR', (e.filename || '').split('/').pop() + ':' + e.lineno + ' ' + e.message);\n"
            "  });\n"
            "})();\n")


def context_menu_script() -> str:
    """WebKit's right-click menu only where it helps: on text fields and selected text (Cut, Copy, Paste)."""
    return ("document.addEventListener('contextmenu', function (e) {\n"
            "  var field = e.target && e.target.closest && e.target.closest('input, textarea, [contenteditable]');\n"
            "  if (!field && !String(window.getSelection())) e.preventDefault();\n"
            "});\n")


def deliver_script(message: str) -> str:
    """JavaScript that hands one QWebChannel message (JSON text) to the page."""
    return ("window.qt && window.qt.webChannelTransport.onmessage && "
            f"window.qt.webChannelTransport.onmessage({{data: {json.dumps(message)}}});")


def qwebchannel_js() -> str:
    """qwebchannel.js from Qt's resources (QtWebChannel carries it)."""
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly):
        raise RuntimeError("qwebchannel.js is missing from Qt's resources")
    try:
        return bytes(f.readAll()).decode("utf-8")
    finally:
        f.close()


class WebKitTransport(QWebChannelAbstractTransport):
    """QWebChannel's side of the bridge: messages from the page come in through receive(), answers go out through
    the view's run_js()."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sendMessage(self, message) -> None:
        if not isinstance(message, (dict, list)):
            from PySide6.QtCore import QJsonDocument
            message = json.loads(bytes(QJsonDocument(message).toJson(QJsonDocument.Compact)).decode("utf-8"))
        self.view.run_js(deliver_script(json.dumps(message, separators=(",", ":"))))

    def receive(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            print(f"[UI WARN] the page sent something that isn't JSON: {text[:80]}")
            return
        if isinstance(message, dict):
            self.messageReceived.emit(message, self)


def install_edit_menu() -> None:
    """Adds the Edit menu (EDIT_MENU) after the application menu Qt makes, once. Qt replaces the main menu only for a
    QMenuBar, and Jarvis has none, so the menu stays."""
    from AppKit import NSApplication, NSEventModifierFlagCommand, NSEventModifierFlagShift, NSMenu, NSMenuItem
    main = NSApplication.sharedApplication().mainMenu()
    if main is None or main.itemWithTitle_("Edit") is not None:
        return
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for entry in EDIT_MENU:
        if entry is None:
            edit.addItem_(NSMenuItem.separatorItem())
            continue
        title, action, key, shift = entry
        item = edit.addItemWithTitle_action_keyEquivalent_(title, action, key)      # no target: the focused view
        if shift:
            item.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand | NSEventModifierFlagShift)
    top = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    top.setSubmenu_(edit)
    main.insertItem_atIndex_(top, min(1, main.numberOfItems()))


def _report(what: str) -> None:
    """An exception must not leave a WebKit callback: PyObjC raises it in WebKit as an Objective-C exception, and
    that ends the app. It goes to the log instead."""
    print(f"[UI ERROR] handling {what}:\n{traceback.format_exc()}", file=sys.stderr, flush=True)


def call_block(block: int, value: int) -> None:
    """Calls the block at address `block` with one NSInteger, through the Block ABI: a block starts with isa (a
    pointer), flags and reserved (two ints), then its invoke function, which takes the block and the arguments."""
    invoke = ctypes.c_void_p.from_address(block + ctypes.sizeof(ctypes.c_void_p) + 2 * ctypes.sizeof(ctypes.c_int))
    ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ssize_t)(invoke.value)(block, value)


def answer(decision, policy: int) -> None:
    """Gives WebKit's decisionHandler block its policy. PyObjC calls a block through a signature taken from the
    block or from PyObjC's WebKit metadata. WebKit's blocks carry none, so when the metadata didn't reach the
    delegate method ("cannot call block without a signature") the block is called directly."""
    if getattr(decision, "__block_signature__", None) is not None:
        decision(policy)
    else:
        import objc
        call_block(objc.pyobjc_id(decision), policy)


def _objc_classes() -> dict:
    """The Objective-C classes WebKit calls back into, defined once (a class name can be registered only once)."""
    if _CLASSES:
        return _CLASSES
    import objc
    import WebKit  # noqa: F401  (loads WebKit's protocols)
    from Foundation import NSObject

    class JarvisScriptHandler(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
        def userContentController_didReceiveScriptMessage_(self, controller, message):
            try:
                view = getattr(self, "view", None)
                if view is not None:
                    view.on_script_message(str(message.name()), str(message.body()))
            except Exception:
                _report("a message from the page")

    # No protocols=[WKNavigationDelegate]: the formal protocol types decisionHandler as a bare block ("@?"), and
    # undeclared the method can take the block's signature from PyObjC's WebKit metadata. answer() works either way.
    class JarvisNavigationDelegate(NSObject):
        def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, decision):
            allowed = False
            try:
                url = action.request().URL()
                view = getattr(self, "view", None)
                allowed = url is not None and view is not None and view.allows(str(url.absoluteString()))
            except Exception:
                _report("a page load")
            try:
                answer(decision, 1 if allowed else 0)   # WKNavigationActionPolicyAllow / Cancel
            except Exception:
                _report("the answer to a page load")

        def webViewWebContentProcessDidTerminate_(self, webview):
            try:
                print("[UI WARN] WebKit's page process ended; reloading the page")
                webview.reload()
            except Exception:
                _report("the page process ending")

    _CLASSES.update(handler=JarvisScriptHandler, navigation=JarvisNavigationDelegate)
    return _CLASSES


class WebKitView(QWidget):
    """A WKWebView showing one of the app's pages, embedded in Qt as a foreign window. page()/runJavaScript() and
    reload() match what qt_app uses from QWebEngineView."""

    def __init__(self, parent, bridge, page: Path, transparent: bool = False):
        super().__init__(parent)
        import objc
        from AppKit import NSColor
        from Foundation import NSURL
        from WebKit import WKUserScript, WKWebView, WKWebViewConfiguration

        self.page_dir = Path(page).resolve().parent
        classes = _objc_classes()
        config = WKWebViewConfiguration.alloc().init()
        content = config.userContentController()
        for source in (transport_script(), console_script(), context_menu_script(), qwebchannel_js()):
            content.addUserScript_(WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(source, 0, True))
        config.setMediaTypesRequiringUserActionForPlayback_(0)     # WKAudiovisualMediaTypeNone: samples play
        self._handler = classes["handler"].alloc().init()
        self._handler.view = self
        content.addScriptMessageHandler_name_(self._handler, HANDLER)
        content.addScriptMessageHandler_name_(self._handler, LOG_HANDLER)

        self._web = WKWebView.alloc().initWithFrame_configuration_(((0, 0), (640, 480)), config)
        self._navigation = classes["navigation"].alloc().init()
        self._navigation.view = self
        self._web.setNavigationDelegate_(self._navigation)
        if transparent:
            try:
                self._web.setValue_forKey_(False, "drawsBackground")
            except Exception as e:                      # a WebKit without the key: the orb gets a background
                print(f"[UI WARN] WebKit can't draw a transparent page: {e}")
        else:
            self._web.setUnderPageBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*BACKGROUND, 1.0))
        if os.environ.get("JARVIS_DEV") == "1" and self._web.respondsToSelector_("setInspectable:"):
            self._web.setInspectable_(True)             # Safari › Develop shows the page (as DevTools would)

        self.channel = QWebChannel(self)
        self.channel.registerObject("jarvis", bridge)
        self.transport = WebKitTransport(self)
        self.channel.connectTo(self.transport)

        container = QWidget.createWindowContainer(QWindow.fromWinId(objc.pyobjc_id(self._web)), self)
        container.setFocusPolicy(Qt.StrongFocus)        # the page takes the keyboard when its window opens
        self.setFocusProxy(container)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(container)
        install_edit_menu()
        self._web.loadFileURL_allowingReadAccessToURL_(NSURL.fileURLWithPath_(str(Path(page).resolve())),
                                                      NSURL.fileURLWithPath_(str(self.page_dir)))

    def allows(self, url: str) -> bool:
        """Only the app's own pages (file URLs inside the page folder) and about:blank load."""
        qurl = QUrl(url)
        if qurl.scheme() == "about":
            return True
        if not qurl.isLocalFile():
            return False
        path = Path(qurl.toLocalFile()).resolve()
        return path == self.page_dir or self.page_dir in path.parents

    def on_script_message(self, name: str, body: str) -> None:
        if name == HANDLER:
            self.transport.receive(body)
        elif name == LOG_HANDLER:
            level, _, text = body.partition(" ")
            print(f"[UI {level}] {text}")

    def run_js(self, code: str) -> None:
        self._web.evaluateJavaScript_completionHandler_(code, None)

    def page(self):
        return self

    def runJavaScript(self, code: str, *_):
        self.run_js(code)

    def reload(self) -> None:
        self._web.reload()


def make_view(parent, bridge, page: Path, transparent: bool = False) -> WebKitView:
    return WebKitView(parent, bridge, page, transparent)
