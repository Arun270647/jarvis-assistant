"""Desktop control tools (src/tools/desktop_tools.py) without touching the real mouse or keyboard.

Window lookup, process names and input are patched, so these tests only check the
safety rules (Jarvis's own windows, consoles, Win-key shortcuts), dry runs and wiring.
"""
import os
import unittest
from unittest import mock

from src.tools import desktop_tools as dt
from src.tools.registry import ToolContext, ToolRegistry

WINDOWS = [
    {"hwnd": 10, "title": "Jarvis", "process": "pythonw.exe", "pid": os.getpid()},
    {"hwnd": 20, "title": "Google Calendar - Google Chrome", "process": "chrome.exe", "pid": 111},
    {"hwnd": 30, "title": "Command Prompt", "process": "cmd.exe", "pid": 222},
    {"hwnd": 40, "title": "jarvis - File Explorer", "process": "explorer.exe", "pid": 333},
]
BY_HWND = {w["hwnd"]: w for w in WINDOWS}


def fake_process(hwnd):
    w = BY_HWND.get(hwnd, {"pid": 0, "process": ""})
    return w["pid"], w["process"]


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(dt, "top_windows", lambda: list(WINDOWS)),
                        mock.patch.object(dt, "process_name", fake_process),
                        mock.patch.object(dt, "focus_window", lambda h: True),
                        mock.patch.object(dt, "window_title", lambda h: BY_HWND.get(h, {}).get("title", "")),
                        mock.patch.object(dt, "click_at", mock.Mock()),
                        mock.patch.object(dt, "type_unicode", mock.Mock()),
                        mock.patch.object(dt, "press_combo", mock.Mock()),
                        mock.patch.object(dt.time, "sleep", lambda s: None)]
        for p in self.patches:
            p.start()
        self.reg = ToolRegistry()
        self.desk = dt.register_desktop_tools(self.reg, {})
        self.ctx = ToolContext()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_tool(self, name, args):
        return self.reg.get(name).handler(args, self.ctx)

    def test_parse_keys(self):
        self.assertEqual(dt.parse_keys("ctrl+shift+t"), [0x11, 0x10, ord("T")])
        self.assertEqual(dt.parse_keys("Enter"), [0x0D])
        self.assertEqual(dt.parse_keys("alt+f4"), [0x12, 0x73])
        for bad in ("win+r", "lwin", "ctrl+banana", ""):
            with self.assertRaises(ValueError):
                dt.parse_keys(bad)

    def test_default_window_skips_jarvis(self):
        w, err = self.desk.pick_window("")
        self.assertEqual(w["hwnd"], 20)
        w, err = self.desk.pick_window("calendar")
        self.assertEqual(w["process"], "chrome.exe")
        w, err = self.desk.pick_window("nothing like this")
        self.assertIsNone(w)
        self.assertIn("Open windows", err)

    def test_refusals(self):
        self.assertIn("Jarvis", self.desk.refusal(10))
        self.assertIn("cmd.exe", self.desk.refusal(30))
        self.assertEqual(self.desk.refusal(40), "")                     # clicking in Explorer is fine
        self.assertIn("Typing", self.desk.refusal(40, typing=True))     # typing in its address bar is not
        self.assertEqual(self.desk.refusal(20, typing=True), "")

    def test_snapshot_refuses_console(self):
        r = self.run_tool("desktop_snapshot", {"window": "command prompt"})
        self.assertEqual(r.status, "denied")

    def test_click_element_from_snapshot(self):
        self.desk.elements = {1: {"name": "Save", "type": "button", "value": "", "rect": (100, 100, 140, 120),
                                  "hwnd": 20, "enabled": True, "focused": False, "password": False}}
        with mock.patch.object(dt, "root_at", lambda x, y: 20):
            r = self.run_tool("desktop_click", {"element": 1})
        self.assertTrue(r.ok, r.content)
        dt.click_at.assert_called_with(120, 110, "left", False)

    def test_click_refused_when_jarvis_covers_the_spot(self):
        self.desk.elements = {1: {"name": "Save", "type": "button", "value": "", "rect": (0, 0, 10, 10),
                                  "hwnd": 20, "enabled": True, "focused": False, "password": False}}
        dt.click_at.reset_mock()
        with mock.patch.object(dt, "root_at", lambda x, y: 10):
            r = self.run_tool("desktop_click", {"element": 1})
        self.assertFalse(r.ok)
        self.assertIn("Jarvis", r.content)
        dt.click_at.assert_not_called()

    def test_unknown_element(self):
        r = self.run_tool("desktop_click", {"element": 7})
        self.assertEqual(r.status, "invalid")

    def test_keys_refuse_win_and_consoles(self):
        self.assertEqual(self.run_tool("desktop_keys", {"keys": "win+r"}).status, "invalid")
        with mock.patch.object(dt.user32, "GetForegroundWindow", lambda: 30):
            self.assertEqual(self.run_tool("desktop_keys", {"keys": "enter"}).status, "denied")
        with mock.patch.object(dt.user32, "GetForegroundWindow", lambda: 20):
            self.assertTrue(self.run_tool("desktop_keys", {"keys": "ctrl+l"}).ok)

    def test_type_without_target_uses_last_window_not_the_front_one(self):
        self.desk.last_hwnd = 20                                         # e.g. after desktop_snapshot of Chrome
        fg = {"h": 30}                                                   # a console is in front

        def focus(h):
            fg["h"] = h
            return True
        with mock.patch.object(dt, "focus_window", focus), \
                mock.patch.object(dt.user32, "IsWindow", lambda h: True), \
                mock.patch.object(dt.user32, "GetForegroundWindow", lambda: fg["h"]):
            r = self.run_tool("desktop_type", {"text": "hi"})
        self.assertTrue(r.ok, r.content)
        self.assertIn("Google Calendar", r.content)
        dt.type_unicode.assert_called_with("hi")

    def test_dry_run_does_not_click(self):
        reg = ToolRegistry()
        desk = dt.register_desktop_tools(reg, {"dry_run": True})
        desk.elements = {1: {"name": "Send", "type": "button", "value": "", "rect": (0, 0, 10, 10),
                             "hwnd": 20, "enabled": True, "focused": False, "password": False}}
        dt.click_at.reset_mock()
        with mock.patch.object(dt, "root_at", lambda x, y: 20):
            r = reg.get("desktop_click").handler({"element": 1}, self.ctx)
        self.assertEqual(r.status, "dry_run")
        dt.click_at.assert_not_called()

    def test_disabled_in_config(self):
        reg = ToolRegistry()
        self.assertIsNone(dt.register_desktop_tools(reg, {"desktop": {"enabled": False}}))
        self.assertEqual(reg.names(), [])


class DesktopWiringTests(unittest.TestCase):
    def test_screenshots_stay_out_of_ui_events(self):
        from src.tools.policy import PolicyExecutor
        from src.tools.registry import ToolResult, ToolSpec
        reg = ToolRegistry()
        reg.register(ToolSpec(name="shot", description="", input_schema={"type": "object", "properties": {}},
                              handler=lambda a, c: ToolResult(ok=True, content="img",
                                                              data={"images": [{"data": "xx"}], "window": "w"})))
        events = []
        ex = PolicyExecutor(reg, lambda: {}, emit=events.append)
        res = ex.execute("shot", {}, ToolContext())
        self.assertEqual(res.data["images"], [{"data": "xx"}])           # Claude still gets the image
        ev = [e for e in events if e["type"] == "tool_result"][0]
        self.assertNotIn("images", ev["data"])
        self.assertEqual(ev["data"]["window"], "w")


if __name__ == "__main__":
    unittest.main()
