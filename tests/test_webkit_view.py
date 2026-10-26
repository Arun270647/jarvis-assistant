"""The Mac window's web view (src/gui/webkit_view.py), the parts that run on any OS: the scripts it injects, the
QWebChannel transport driven through a real QWebChannel the way qwebchannel.js drives it, the navigation rule and
the answer to WebKit's decision block, the Edit menu's table and the pages' CSS for Safari's engine. The WKWebView itself needs a Mac: the Mac build checks it
(WEBKIT_PROBE in packaging/build_macos.py)."""
import ctypes
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QObject, Slot
from PySide6.QtWebChannel import QWebChannel

from src.gui import webkit_view

APP = QCoreApplication.instance() or QCoreApplication([])


class FakeView(QObject):
    """Stands in for WebKitView: collects the JavaScript the transport would run in the page."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def run_js(self, code):
        self.sent.append(code)

    def last_message(self) -> dict:
        code = self.sent[-1]
        literal = code[code.index("{data: ") + len("{data: "):code.rindex("})")]
        return json.loads(json.loads(literal))


class Bridge(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []

    @Slot(str, result=str)
    def hello(self, text):
        self.calls.append(text)
        return text.upper()


class ScriptTests(unittest.TestCase):
    def test_transport_script_posts_to_the_message_handler(self):
        script = webkit_view.transport_script()
        self.assertIn("window.qt.webChannelTransport", script)
        self.assertIn(f"window.webkit.messageHandlers.{webkit_view.HANDLER}.postMessage(message)", script)

    def test_console_script_forwards_warnings_errors_and_exceptions(self):
        script = webkit_view.console_script()
        self.assertIn(webkit_view.LOG_HANDLER, script)
        self.assertIn("'warn', 'error'", script)
        self.assertIn("addEventListener('error'", script)

    def test_messages_reach_the_page_as_a_safe_string(self):
        tricky = json.dumps({"text": "quote \" and </script> and   line"})
        code = webkit_view.deliver_script(tricky)
        self.assertTrue(code.startswith("window.qt && window.qt.webChannelTransport.onmessage"))
        literal = code[code.index("{data: ") + len("{data: "):code.rindex("})")]
        self.assertEqual(json.loads(literal), tricky)

    def test_context_menu_only_on_text_fields_and_selections(self):
        script = webkit_view.context_menu_script()
        self.assertIn("addEventListener('contextmenu'", script)
        self.assertIn("closest('input, textarea, [contenteditable]')", script)
        self.assertIn("window.getSelection()", script)
        self.assertIn("e.preventDefault()", script)

    def test_qwebchannel_js_comes_from_qt(self):
        self.assertIn("QWebChannel", webkit_view.qwebchannel_js())


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.view, self.bridge = FakeView(), Bridge()
        self.channel = QWebChannel(self.view)
        self.channel.registerObject("jarvis", self.bridge)
        self.transport = webkit_view.WebKitTransport(self.view)
        self.channel.connectTo(self.transport)

    def send(self, message: dict) -> dict:
        self.transport.receive(json.dumps(message))
        APP.processEvents()
        return self.view.last_message()

    def test_init_publishes_the_bridge_and_its_methods(self):
        reply = self.send({"type": 3, "id": 0})                      # QWebChannelMessageTypes.init
        self.assertEqual((reply["type"], reply["id"]), (10, 0))       # response
        methods = [m[0] for m in reply["data"]["jarvis"]["methods"]]
        self.assertIn("hello", methods)

    def test_a_call_from_the_page_runs_the_slot_and_answers(self):
        self.send({"type": 3, "id": 0})
        reply = self.send({"type": 6, "object": "jarvis", "method": "hello", "args": ["hi"], "id": 1})  # invokeMethod
        self.assertEqual(self.bridge.calls, ["hi"])
        self.assertEqual(reply, {"type": 10, "id": 1, "data": "HI"})

    def test_something_that_isnt_json_is_ignored(self):
        self.transport.receive("not json")
        APP.processEvents()
        self.assertEqual(self.view.sent, [])


class EditMenuTests(unittest.TestCase):
    def test_the_mac_text_shortcuts_are_in_the_edit_menu(self):
        keys = {action: (key, shift) for _, action, key, shift in filter(None, webkit_view.EDIT_MENU)}
        self.assertEqual(keys, {"undo:": ("z", False), "redo:": ("z", True), "cut:": ("x", False),
                                "copy:": ("c", False), "paste:": ("v", False), "selectAll:": ("a", False)})


class PageCssTests(unittest.TestCase):
    def test_every_user_select_has_the_webkit_prefix(self):
        """Safari's engine (the Mac window) reads -webkit-user-select, so each rule needs both spellings."""
        css = (Path(webkit_view.__file__).parent / "web" / "app.css").read_text(encoding="utf-8")
        plain = re.findall(r"(?<![-\w])user-select: *(\w+)", css)
        prefixed = re.findall(r"-webkit-user-select: *(\w+)", css)
        self.assertTrue(plain)
        self.assertEqual(sorted(plain), sorted(prefixed))


INVOKE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ssize_t)


class BlockLiteral(ctypes.Structure):
    """A block as the Block ABI lays it out, without a signature (as WebKit's are)."""
    _fields_ = [("isa", ctypes.c_void_p), ("flags", ctypes.c_int), ("reserved", ctypes.c_int),
                ("invoke", INVOKE), ("descriptor", ctypes.c_void_p)]


class DecisionBlockTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.invoke = INVOKE(lambda block, value: self.calls.append((block, value)))
        self.block = BlockLiteral(None, 0, 0, self.invoke, None)
        self.address = ctypes.addressof(self.block)

    def test_a_block_is_called_through_its_invoke_function(self):
        webkit_view.call_block(self.address, 1)
        self.assertEqual(self.calls, [(self.address, 1)])

    def test_without_a_signature_the_answer_goes_through_the_invoke_function(self):
        decision = type("Block", (), {"__block_signature__": None})()
        fake_objc = types.SimpleNamespace(pyobjc_id=lambda obj: self.address if obj is decision else 0)
        with mock.patch.dict(sys.modules, {"objc": fake_objc}):
            webkit_view.answer(decision, 0)
        self.assertEqual(self.calls, [(self.address, 0)])

    def test_with_a_signature_pyobjc_calls_the_block(self):
        decision = mock.Mock(__block_signature__=object())
        webkit_view.answer(decision, 1)
        decision.assert_called_once_with(1)
        self.assertEqual(self.calls, [])


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "web").mkdir()
        self.view = type("View", (), {"page_dir": (self.dir / "web").resolve()})()

    def allows(self, url: str) -> bool:
        return webkit_view.WebKitView.allows(self.view, url)

    def test_only_the_apps_own_pages_load(self):
        web = (self.dir / "web").resolve().as_uri()
        self.assertTrue(self.allows(web + "/index.html"))
        self.assertTrue(self.allows(web + "/fonts/barlow.woff2"))
        self.assertTrue(self.allows("about:blank"))
        self.assertFalse(self.allows(self.dir.resolve().as_uri() + "/other.html"))
        self.assertFalse(self.allows("https://example.com/"))
        self.assertFalse(self.allows("javascript:alert(1)"))


if __name__ == "__main__":
    unittest.main()
