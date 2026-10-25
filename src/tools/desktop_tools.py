"""Desktop control for Jarvis: see and operate windows the way a person does.

- desktop_snapshot reads a window's accessibility tree (buttons, links, fields, text) and numbers the
  elements; desktop_click / desktop_type act on those numbers. Chrome exposes web pages to it, so this works
  inside the user's real Chrome profiles (with their logins) on pages opened by open_chrome, and in any other app.
- desktop_screenshot returns an image of a window for Claude to look at; desktop_click x/y
  are pixels in the latest screenshot.
- Input is real mouse and keyboard. Jarvis's own windows (its approval dialog),
  consoles, password managers, desktop.blocked_processes and windows whose title matches
  desktop.blocked_titles (browser password pages, Credential Manager, Keychain Access) are refused, typing is
  refused in desktop.no_typing_processes (Start menu, Explorer's address bar, Spotlight) and shortcuts that open a
  launcher (the Windows key, Command-Space) are not sent, so desktop control cannot get around the approval and
  command policies. (Terminals Jarvis opens are typed into through terminal_tools, under the command policy.)

This module holds the tools and the safety rules; desktop_win.py (UI Automation, SendInput) and desktop_mac.py
(the Accessibility API, Quartz events) talk to the OS. Their functions are imported here by name, so the tests
can replace them.
"""
import base64
import io
import os
import sys
import threading
import time

from src.tools.registry import ToolResult, ToolSpec

if sys.platform == "darwin":
    from src.tools.desktop_mac import (DELETE, ENTER, KEYS_HELP, SELECT_ALL, UI_TREE, click_at, focus_window,
                                       foreground, is_window, missing_permission, parse_keys, press_combo,
                                       process_name, read_elements, root_at, screen_box, scroll_at, thread_scope,
                                       top_windows, type_unicode, window_rect, window_title)
else:
    from src.tools.desktop_win import (DELETE, ENTER, KEYS_HELP, SELECT_ALL, UI_TREE, click_at, focus_window,
                                       foreground, is_window, missing_permission, parse_keys, press_combo,
                                       process_name, read_elements, root_at, screen_box, scroll_at, thread_scope,
                                       top_windows, type_unicode, window_rect, window_title)

BLOCKED_PROCESSES = ["cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe",
                     "openconsole.exe", "wsl.exe", "bash.exe", "regedit.exe", "mmc.exe", "taskmgr.exe",
                     "consent.exe", "credentialuibroker.exe", "lockapp.exe",
                     # password managers
                     "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "lastpass.exe",
                     "dashlane.exe", "nordpass.exe", "keeperpasswordmanager.exe", "roboform.exe", "enpass.exe",
                     "proton pass.exe", "protonpass.exe", "pwsafe.exe"]
# Window titles (lower case, part of the title) that are refused whatever the app: browsers' saved-password
# and payment pages, Windows Credential Manager.
BLOCKED_TITLES = ["password manager", "about:logins", "logins & passwords", "logins and passwords",
                  "settings - passwords", "settings - autofill", "payment methods", "microsoft wallet",
                  "credential manager"]
CONSOLE_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe", "openconsole.exe"}
NO_TYPING_PROCESSES = ["explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe",
                       "shellexperiencehost.exe", "searchapp.exe"]
WINDOWS_RULES = {"blocked_processes": BLOCKED_PROCESSES, "blocked_titles": BLOCKED_TITLES,
                 "no_typing_processes": NO_TYPING_PROCESSES, "consoles": CONSOLE_PROCESSES}

# macOS: a process is named by its program in lower case ("terminal", "1password").
MAC_CONSOLES = {"terminal", "iterm2", "warp", "stable", "alacritty", "kitty", "wezterm-gui", "hyper", "ghostty",
                "tabby"}
MAC_RULES = {
    "blocked_processes": sorted(MAC_CONSOLES) + [
        "script editor", "automator", "shortcuts", "system settings", "system preferences", "activity monitor",
        "keychain access", "passwords", "securityagent", "coreautha", "loginwindow", "installer",
        # password managers
        "1password", "1password 7", "bitwarden", "keepassxc", "lastpass", "dashlane", "nordpass",
        "keeper password manager", "roboform", "enpass", "proton pass", "strongbox", "secrets"],
    "blocked_titles": BLOCKED_TITLES + ["keychain access", "passwords"],
    "no_typing_processes": ["spotlight", "dock", "systemuiserver", "controlcenter", "notificationcenter", "alfred",
                            "raycast", "launchbar"],
    "consoles": MAC_CONSOLES,
}
RULES = MAC_RULES if sys.platform == "darwin" else WINDOWS_RULES


# ------------------------------------------------------------------ the tools

class Desktop:
    """Shared state between calls: the last snapshot's numbered elements and last screenshot geometry."""

    def __init__(self, config: dict):
        cfg = config.get("desktop", {}) or {}
        self.config = config
        self.blocked = {p.lower() for p in cfg.get("blocked_processes", RULES["blocked_processes"])}
        self.blocked_titles = [t.lower() for t in cfg.get("blocked_titles", RULES["blocked_titles"])]
        self.no_typing = {p.lower() for p in cfg.get("no_typing_processes", RULES["no_typing_processes"])}
        self.consoles = set(RULES["consoles"])
        self.max_side = int(cfg.get("screenshot_max_side", 1400))
        self.own_pid = os.getpid()
        self.elements = {}            # id -> {name, type, rect, hwnd}
        self.shot = None              # {hwnd, left, top, scale}
        self.last_hwnd = 0            # the window Jarvis last looked at or acted in
        self.lock = threading.Lock()

    # -- window selection and safety

    def refusal(self, hwnd: int, typing: bool = False) -> str:
        pid, proc = process_name(hwnd)
        if pid == self.own_pid:
            return "That is a Jarvis window. Jarvis does not operate its own windows (approvals stay with the user)."
        if proc in self.blocked:
            hint = (" For a terminal, use open_terminal, then terminal_type / terminal_read."
                    if proc in self.consoles else "")
            return f"Desktop control is not allowed in {proc} (desktop.blocked_processes).{hint}"
        title = window_title(hwnd).lower()
        hit = next((t for t in self.blocked_titles if t in title), None)
        if hit:
            return (f"Desktop control is not allowed in windows about saved passwords or payments (title contains "
                    f"“{hit}”, desktop.blocked_titles).")
        if typing and proc in self.no_typing:
            return f"Typing into {proc} is not allowed (desktop.no_typing_processes). Use the dedicated tools instead."
        return ""

    def pick_window(self, query: str) -> tuple:
        """(window dict, error). Empty query = the frontmost window that is not Jarvis."""
        wins = [w for w in top_windows() if w["pid"] != self.own_pid]
        if not wins:
            return None, "No windows are open."
        q = (query or "").strip().lower()
        if not q:
            return wins[0], ""
        for test in (lambda w: w["title"].lower() == q, lambda w: q in w["title"].lower(),
                     lambda w: w["process"] in (q, q + ".exe")):
            hits = [w for w in wins if test(w)]
            if hits:
                return hits[0], ""
        return None, f"No window matches '{query}'. Open windows: " + "; ".join(
            f"{w['title'][:60]} ({w['process']})" for w in wins[:15])

    def prepare(self, query: str, typing: bool = False) -> tuple:
        """(window, error): the window named by query, else the one Jarvis last worked in, else
        the frontmost non-Jarvis window; checked against the blocklists and brought to the front."""
        if not query and self.last_hwnd and is_window(self.last_hwnd):
            pid, proc = process_name(self.last_hwnd)
            w = {"hwnd": self.last_hwnd, "title": window_title(self.last_hwnd), "process": proc, "pid": pid}
        else:
            w, err = self.pick_window(query)
            if w is None:
                return None, err
        err = self.refusal(w["hwnd"], typing)
        if err:
            return None, err
        focus_window(w["hwnd"])        # some windows refuse focus but still accept input
        self.last_hwnd = w["hwnd"]
        return w, ""

    # -- reading

    def snapshot(self, hwnd: int, query: str = "", include_text: bool = True, limit: int = 150) -> list:
        return read_elements(hwnd, query, include_text, limit)

    def screenshot(self, hwnd: int) -> dict:
        from PIL import ImageGrab
        l, t, r, b = screen_box(hwnd)
        # Screen coordinates: pixels on Windows, points on macOS (Pillow scales a Retina capture to points), the
        # units the mouse uses on each.
        img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True).convert("RGB")
        scale = min(1.0, self.max_side / max(img.width, img.height))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72)
        self.shot = {"hwnd": hwnd, "left": l, "top": t, "scale": scale}
        return {"data": base64.b64encode(buf.getvalue()).decode("ascii"), "mimeType": "image/jpeg",
                "width": img.width, "height": img.height}


def _line(i: int, el: dict) -> str:
    s = f"[{i}] {el['type']} \"{el['name']}\""
    if el["value"]:
        s += f" = \"{el['value']}\""
    if el["password"]:
        s += " (password field)"
    if not el["enabled"]:
        s += " (disabled)"
    if el["focused"]:
        s += " (focused)"
    return s


def register_desktop_tools(reg, config: dict) -> Desktop:
    if not (config.get("desktop", {}) or {}).get("enabled", True):
        return None
    d = Desktop(config)

    def com(fn, screen: bool = False):
        """Run a tool inside the OS's per-thread setup, once the OS allows desktop control (macOS asks)."""
        def run(a, c):
            missing = missing_permission(screen)
            if missing:
                return ToolResult(ok=False, status="failed", content=missing)
            with thread_scope():
                return fn(a, c)
        return run

    def dry(what: str):
        return ToolResult(ok=True, status="dry_run", dry_run=True, content=f"Would {what}") \
            if config.get("dry_run") else None

    def after(a):
        time.sleep(min(int(a.get("wait_ms", 400)), 5000) / 1000.0)

    def desktop_windows(a, c):
        wins = [w for w in top_windows() if w["pid"] != d.own_pid]
        fg = foreground()
        lines = [f"- {w['title'][:100]} ({w['process']})" + (" — in front" if w["hwnd"] == fg else "") for w in wins]
        return ToolResult(ok=True, content="Open windows, front to back:\n" + "\n".join(lines),
                          data={"windows": [{k: w[k] for k in ("title", "process")} for w in wins]})

    def desktop_snapshot(a, c):
        w, err = d.pick_window(a.get("window", ""))
        if w is None:
            return ToolResult(ok=False, status="failed", content=err)
        err = d.refusal(w["hwnd"])
        if err:
            return ToolResult(ok=False, status="denied", content=err)
        d.last_hwnd = w["hwnd"]
        if a.get("focus", True):
            focus_window(w["hwnd"])
            time.sleep(0.15)
        els = d.snapshot(w["hwnd"], a.get("query", ""), a.get("include_text", True), int(a.get("max_elements", 150)))
        with d.lock:
            d.elements = {i: el for i, el in enumerate(els, 1)}
        lines = [f"Window: {w['title']} ({w['process']})",
                 f"{len(els)} visible elements" + (f" matching '{a['query']}'" if a.get("query") else "") +
                 ". Act on them with desktop_click / desktop_type by number:"]
        lines += [_line(i, el) for i, el in d.elements.items()]
        if not els:
            lines.append("(Nothing readable. Try desktop_screenshot, scroll, or a different query.)")
        res = ToolResult(ok=True, untrusted=True, content="\n".join(lines), data={"window": w["title"]})
        if a.get("screenshot"):
            missing = missing_permission(screen=True)
            if missing:
                res.content += f"\n(No screenshot: {missing})"
            else:
                res.data["images"] = [d.screenshot(w["hwnd"])]
        return res

    def desktop_screenshot(a, c):
        w, err = d.pick_window(a.get("window", ""))
        if w is None:
            return ToolResult(ok=False, status="failed", content=err)
        err = d.refusal(w["hwnd"])
        if err:
            return ToolResult(ok=False, status="denied", content=err)
        d.last_hwnd = w["hwnd"]
        focus_window(w["hwnd"])
        time.sleep(0.2)
        img = d.screenshot(w["hwnd"])
        return ToolResult(ok=True, untrusted=True,
                          content=f"Screenshot of {w['title']} ({w['process']}), {img['width']}x{img['height']} "
                                  "pixels. desktop_click x/y use these pixels.",
                          data={"images": [img], "window": w["title"]})

    def target_point(a) -> tuple:
        """(x, y, hwnd, label, error) on the real screen."""
        if a.get("element") is not None:
            with d.lock:
                el = d.elements.get(int(a["element"]))
            if el is None:
                return None, None, 0, "", f"No element {a['element']} in the latest snapshot. Take a new desktop_snapshot."
            l, t, r, b = el["rect"]
            return (l + r) // 2, (t + b) // 2, el["hwnd"], f"{el['type']} \"{el['name']}\"", ""
        if a.get("x") is not None and a.get("y") is not None:
            if not d.shot:
                return None, None, 0, "", "x/y need a desktop_screenshot first (they are pixels in that screenshot)."
            s = d.shot
            return (int(s["left"] + a["x"] / s["scale"]), int(s["top"] + a["y"] / s["scale"]), s["hwnd"],
                    f"point ({a['x']}, {a['y']})", "")
        return None, None, 0, "", "Give element (a number from desktop_snapshot) or x and y (screenshot pixels)."

    def checked_target(a, typing=False):
        x, y, hwnd, label, err = target_point(a)
        if err:
            return None, ToolResult(ok=False, status="invalid", content=err)
        err = d.refusal(hwnd, typing)
        if err:
            return None, ToolResult(ok=False, status="denied", content=err)
        focus_window(hwnd)
        d.last_hwnd = hwnd
        time.sleep(0.1)
        under = root_at(x, y)
        if under and under != hwnd:
            err = d.refusal(under, typing) or (
                f"Another window ({window_title(under)[:60]}) covers that spot. Take a new snapshot.")
            return None, ToolResult(ok=False, status="failed", content=err)
        return (x, y, hwnd, label), None

    def desktop_click(a, c):
        tgt, bad = checked_target(a)
        if bad:
            return bad
        x, y, hwnd, label = tgt
        what = f"{'double-' if a.get('double') else ''}{a.get('button', 'left')}-click {label} in {window_title(hwnd)[:60]}"
        r = dry(what)
        if r:
            return r
        click_at(x, y, a.get("button", "left"), bool(a.get("double")))
        after(a)
        return ToolResult(ok=True, content=f"Clicked {label}. Take a new desktop_snapshot to see the result.")

    def desktop_type(a, c):
        if a.get("element") is not None or a.get("x") is not None:
            tgt, bad = checked_target(a, typing=True)
            if bad:
                return bad
            x, y, hwnd, label = tgt
        else:
            w, err = d.prepare(a.get("window", ""), typing=True)
            if w is None:
                return ToolResult(ok=False, status="denied" if "allowed" in err or "Jarvis" in err else "failed",
                                  content=err)
            hwnd, x, y = w["hwnd"], None, None
            label = f"the focused field in {w['title'][:60]}"
            time.sleep(0.1)
        r = dry(f"type {len(a['text'])} characters into {label}")
        if r:
            return r
        if x is not None:
            click_at(x, y)
            time.sleep(0.15)
        if a.get("clear"):
            press_combo(SELECT_ALL)
            press_combo(DELETE)
        if foreground() != hwnd or d.refusal(foreground(), typing=True):
            return ToolResult(ok=False, status="failed", content="The target window lost focus; nothing was typed.")
        type_unicode(a["text"])
        if a.get("submit"):
            press_combo(ENTER)
        after(a)
        return ToolResult(ok=True, content=f"Typed into {label}" + (" and pressed Enter." if a.get("submit") else "."))

    def desktop_keys(a, c):
        try:
            vks = parse_keys(a["keys"])
        except ValueError as e:
            return ToolResult(ok=False, status="invalid", content=str(e))
        w, err = d.prepare(a.get("window", ""), typing=True)
        if w is None:
            return ToolResult(ok=False, status="denied" if "allowed" in err or "Jarvis" in err else "failed",
                              content=err)
        time.sleep(0.1)
        hwnd = foreground()
        err = d.refusal(hwnd, typing=True)
        if err:
            return ToolResult(ok=False, status="denied", content=err)
        r = dry(f"press {a['keys']} in {window_title(hwnd)[:60]}")
        if r:
            return r
        for _ in range(int(a.get("times", 1))):
            press_combo(vks)
            time.sleep(0.05)
        after(a)
        return ToolResult(ok=True, content=f"Pressed {a['keys']} in {window_title(hwnd)[:60]}.")

    def desktop_scroll(a, c):
        if a.get("element") is not None or a.get("x") is not None:
            tgt, bad = checked_target(a)
            if bad:
                return bad
            x, y, hwnd, _ = tgt
        else:
            w, err = d.prepare(a.get("window", ""))
            if w is None:
                return ToolResult(ok=False, status="failed", content=err)
            l, t, r_, b = window_rect(w["hwnd"])
            x, y, hwnd = (l + r_) // 2, (t + b) // 2, w["hwnd"]
        amount = int(a.get("amount", 5))
        r = dry(f"scroll {a['direction']} {amount} in {window_title(hwnd)[:60]}")
        if r:
            return r
        scroll_at(x, y, amount if a["direction"] == "up" else -amount)
        after(a)
        return ToolResult(ok=True, content=f"Scrolled {a['direction']} by {amount}.")

    def desktop_focus(a, c):
        w, err = d.prepare(a["window"])
        if w is None:
            return ToolResult(ok=False, status="failed", content=err)
        return ToolResult(ok=True, content=f"{w['title']} ({w['process']}) is in front.")

    window_arg = {"type": "string", "maxLength": 200,
                  "description": "Part of the window title or the process name (e.g. 'Google Calendar', 'chrome', "
                                 "'notepad'). Empty = the frontmost window that is not Jarvis."}
    target = {"element": {"type": "integer", "minimum": 1, "description": "Element number from desktop_snapshot"},
              "x": {"type": "number", "minimum": 0, "description": "Pixel x in the latest desktop_screenshot"},
              "y": {"type": "number", "minimum": 0},
              "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000,
                          "description": "Wait after acting (default 400 ms) so the page can react"}}
    common = dict(risk="sensitive", timeout_s=30)

    reg.register(ToolSpec(
        name="desktop_windows", description="List the open windows on the computer (title and process), front to back.",
        input_schema={"type": "object", "properties": {}}, handler=com(desktop_windows), category="read",
        purpose=lambda a: "List open windows"))
    reg.register(ToolSpec(
        name="desktop_snapshot",
        description=f"Read a window's visible controls and text through {UI_TREE} and number them, so "
                    "desktop_click / desktop_type can act on them. Works on web pages in the user's real Chrome "
                    "(after open_chrome) with their logins, and on any desktop app. query filters by text. "
                    "screenshot=true also returns an image. Take a new snapshot after every action that changes "
                    "the screen; numbers are only valid for the latest snapshot.",
        input_schema={"type": "object", "properties": {
            "window": window_arg,
            "query": {"type": "string", "maxLength": 200},
            "include_text": {"type": "boolean", "description": "Include plain text and images (default true)"},
            "max_elements": {"type": "integer", "minimum": 10, "maximum": 400},
            "screenshot": {"type": "boolean"},
            "focus": {"type": "boolean", "description": "Bring the window to the front first (default true)"}}},
        handler=com(desktop_snapshot), category="read", **common,
        purpose=lambda a: f"Look at {a.get('window') or 'the front window'}"))
    reg.register(ToolSpec(
        name="desktop_screenshot",
        description="Take a screenshot of a window and look at it. Use when desktop_snapshot does not show what you "
                    "need (canvas, images, custom widgets); desktop_click x/y are pixels in this image.",
        input_schema={"type": "object", "properties": {"window": window_arg}},
        handler=com(desktop_screenshot, screen=True), category="read", **common,
        purpose=lambda a: f"Screenshot {a.get('window') or 'the front window'}"))
    reg.register(ToolSpec(
        name="desktop_click",
        description="Click with the real mouse on an element from the latest desktop_snapshot (element) or a point "
                    "in the latest desktop_screenshot (x, y). Before a click that sends, buys, deletes or publishes "
                    "something, make sure the user asked for exactly that.",
        input_schema={"type": "object", "properties": dict(target, **{
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "double": {"type": "boolean"}})},
        handler=com(desktop_click), **common,
        purpose=lambda a: f"Click {'element ' + str(a['element']) if a.get('element') else (a.get('x'), a.get('y'))}"))
    reg.register(ToolSpec(
        name="desktop_type",
        description="Type text with the real keyboard. With element (or x/y) it clicks that field first; otherwise "
                    "it types into the focused field of window (default: the window you last looked at or acted in). clear=true replaces the field's text; submit=true presses "
                    "Enter after.",
        input_schema={"type": "object", "properties": dict(target, **{
            "text": {"type": "string", "minLength": 1, "maxLength": 5000}, "window": window_arg,
            "clear": {"type": "boolean"}, "submit": {"type": "boolean"}}), "required": ["text"]},
        handler=com(desktop_type), **common,
        purpose=lambda a: f"Type “{a.get('text', '')[:60]}”"))
    reg.register(ToolSpec(
        name="desktop_keys",
        description="Press a key or shortcut in window (default: the window you last looked at or acted in): "
                    f"{KEYS_HELP} times repeats it.",
        input_schema={"type": "object", "properties": {
            "keys": {"type": "string", "minLength": 1, "maxLength": 60}, "window": window_arg,
            "times": {"type": "integer", "minimum": 1, "maximum": 30},
            "wait_ms": target["wait_ms"]}, "required": ["keys"]},
        handler=com(desktop_keys), **common, purpose=lambda a: f"Press {a.get('keys')}"))
    reg.register(ToolSpec(
        name="desktop_scroll",
        description="Scroll with the mouse wheel over an element, a screenshot point, or the middle of a window.",
        input_schema={"type": "object", "properties": dict(target, **{
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Wheel notches (default 5)"},
            "window": window_arg}), "required": ["direction"]},
        handler=com(desktop_scroll), **common, purpose=lambda a: f"Scroll {a.get('direction')}"))
    reg.register(ToolSpec(
        name="desktop_focus", description="Bring a window to the front.",
        input_schema={"type": "object", "properties": {"window": window_arg}, "required": ["window"]},
        handler=com(desktop_focus), **common, purpose=lambda a: f"Focus {a.get('window')}"))
    return d
