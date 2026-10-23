"""The phone app's server (src/remote/phone_server.py): the real aiohttp server on 127.0.0.1 with a
stub controller and a real MemoryStore, and an aiohttp client playing the phone."""
import asyncio
import base64
import json
import threading
import time
import unittest
from pathlib import Path

import aiohttp

from src.memory.store import MemoryStore
from src.remote import phone_server
from src.remote.phone_server import CLOSE_REVOKED, CLOSE_UNAUTHORIZED, PhoneServer, resolve_bind_host, token_hash
from tests.fakes import temp_db, wait_for


class StubBroker:
    pending = None


class StubController:
    def __init__(self, memory):
        self.memory = memory
        self.broker = StubBroker()
        self.listeners = []
        self.remote = []
        self.approvals = []
        self.result = "sent"
        self.muted = False
        self.cancelled = False
        self.task_calls = []

    def add_listener(self, fn):
        self.listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    def emit(self, ev):
        for fn in list(self.listeners):
            fn(ev)

    def send_remote(self, text="", audio_path="", source="phone", ref=""):
        self.remote.append({"text": text, "audio": audio_path, "source": source, "ref": ref})
        return self.result

    def approve(self, rid, allowed, via="ui"):
        self.approvals.append((rid, allowed, via))
        return True

    def cancel(self):
        self.cancelled = True
        return True

    def set_muted(self, muted):
        self.muted = muted

    def start(self):
        return True

    def stop(self, wait=15.0):
        return True

    def status(self):
        return {"running": True, "phase": "idle", "busy": False, "muted": self.muted, "active_task_id": None,
                "queued": 0, "pending_approval": None, "model": "fake-model", "voice": True, "dry_run": True, "pc": "TEST"}

    def pause_task(self, tid):
        self.task_calls.append(("pause", tid))
        return "paused"

    def cancel_task(self, tid):
        return "cancelled"

    def retry_task(self, tid):
        return "queued"

    def resume_task(self, tid, answer=""):
        self.task_calls.append(("resume", tid, answer))
        return "sent"


class Phone:
    """A test client speaking the app's protocol."""

    def __init__(self, url):
        self.url = url
        self.session = None
        self.ws = None
        self.inbox = []
        self._n = 0

    async def open(self):
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url)
        return self

    async def send(self, **msg):
        self._n += 1
        msg.setdefault("id", f"m{self._n}")
        await self.ws.send_str(json.dumps(msg))
        return msg["id"]

    async def recv(self, timeout=5):
        msg = await asyncio.wait_for(self.ws.receive(), timeout)
        if msg.type == aiohttp.WSMsgType.CLOSE:
            # The code the server sent. ws.close_code can turn into 1006 afterwards: aiohttp's automatic close
            # reply fails when the server has already dropped the connection.
            return {"type": "_closed", "code": msg.data}
        if msg.type != aiohttp.WSMsgType.TEXT:
            return {"type": "_closed", "code": self.ws.close_code}
        data = json.loads(msg.data)
        self.inbox.append(data)
        return data

    async def until(self, pred, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            m = await self.recv(max(0.05, end - time.time()))
            if pred(m):
                return m
        raise AssertionError("message not received")

    async def request(self, **msg):
        mid = await self.send(**msg)
        return await self.until(lambda m: m.get("type") == "ack" and m.get("id") == mid)

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
        if self.session is not None:
            await self.session.close()


class PhoneServerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"phone": {"enabled": True, "port": 0, "bind": "tailscale", "speak_replies": False,
                              "allow_screenshot": True, "approval_timeout_s": 180, "max_upload_mb": 1}}
        self.memory = MemoryStore(temp_db())
        self.controller = StubController(self.memory)
        self.changes = []
        self.paired = []
        self.server = PhoneServer(self.controller, load_config=lambda: self.cfg,
                                  on_change=lambda: self.changes.append(1), screenshot=lambda: b"\xff\xd8JPEG",
                                  on_pair=self.paired.append)
        self.assertTrue(self.server.start(host="127.0.0.1", port=0), self.server.error)
        self.url = f"http://127.0.0.1:{self.server.port}/ws"

    def tearDown(self):
        self.server.stop()
        self.memory.close()

    def run_async(self, coro):
        return asyncio.run(asyncio.wait_for(coro, 20))

    async def paired_phone(self):
        code = self.server.new_pair_code()["code"]
        phone = await Phone(self.url).open()
        ack = await phone.request(type="pair", code=code, device_name="Pixel 8")
        self.assertTrue(ack["ok"], ack)
        await phone.until(lambda m: m["type"] == "welcome")
        return phone, ack["result"]

    # ------------------------------------------------------------ auth

    def test_health_is_open_but_says_nothing(self):
        async def go():
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{self.server.port}/health") as r:
                    return await r.json()
        self.assertEqual(self.run_async(go()), {"name": "jarvis", "version": 1})

    def test_connection_without_hello_is_closed(self):
        async def go():
            phone = await Phone(self.url).open()
            await phone.send(type="command", text="open notepad")
            m = await phone.recv()
            await phone.close()
            return m
        m = self.run_async(go())
        self.assertEqual(m["type"], "_closed")
        self.assertEqual(m["code"], CLOSE_UNAUTHORIZED)
        self.assertEqual(self.controller.remote, [])

    def test_pairing_stores_only_the_token_hash_and_hello_works(self):
        async def go():
            phone, creds = await self.paired_phone()
            await phone.close()
            again = await Phone(self.url).open()
            await again.send(type="hello", device_id=creds["device_id"], token=creds["token"])
            welcome = await again.until(lambda m: m["type"] == "welcome")
            await again.close()
            bad = await Phone(self.url).open()
            await bad.send(type="hello", device_id=creds["device_id"], token="wrong")
            closed = await bad.recv()
            await bad.close()
            return creds, welcome, closed
        creds, welcome, closed = self.run_async(go())
        dev = self.memory.get_phone_device(creds["device_id"])
        self.assertEqual(dev["name"], "Pixel 8")
        self.assertEqual(dev["token_sha256"], token_hash(creds["token"]))
        self.assertNotIn(creds["token"], json.dumps(dev))
        self.assertIsNotNone(dev["last_seen"])
        self.assertEqual(welcome["status"]["model"], "fake-model")
        self.assertEqual(closed["code"], CLOSE_UNAUTHORIZED)
        self.assertIsNone(self.server.pair)                     # the code works once
        self.assertEqual(self.paired, ["Pixel 8"], "a new pairing is announced on the desktop")

    def test_wrong_and_expired_codes_fail_and_tries_are_limited(self):
        async def attempt(code):
            phone = await Phone(self.url).open()
            ack = await phone.request(type="pair", code=code, device_name="x")
            await phone.close()
            return ack
        info = self.server.new_pair_code()
        wrong = "000000" if info["code"] != "000000" else "111111"
        for _ in range(phone_server.PAIR_MAX_TRIES):
            self.assertFalse(self.run_async(attempt(wrong))["ok"])
        self.assertIsNone(self.server.pair)
        self.assertIn("127.0.0.1", self.server.locked_until, "and the address is locked out for a while")
        self.server.locked_until.clear()
        self.assertFalse(self.run_async(attempt(info["code"]))["ok"])          # closed after too many tries
        info = self.server.new_pair_code()
        self.server.pair["expires"] = time.time() - 1
        self.assertFalse(self.run_async(attempt(info["code"]))["ok"])
        self.assertEqual(self.memory.list_phone_devices(), [])

    def test_an_address_with_many_failed_hellos_is_locked_out(self):
        async def hello(token):
            phone = await Phone(self.url).open()
            await phone.send(type="hello", device_id=creds["device_id"], token=token)
            m = await phone.recv()
            await phone.close()
            return m

        async def setup():
            phone, c = await self.paired_phone()
            await phone.close()
            return c
        creds = self.run_async(setup())
        for _ in range(phone_server.AUTH_FAILS_MAX):
            self.assertEqual(self.run_async(hello("wrong"))["code"], CLOSE_UNAUTHORIZED)
        self.assertEqual(self.run_async(hello(creds["token"]))["code"], CLOSE_UNAUTHORIZED,
                         "even the right token is refused while locked out")
        self.server.locked_until["127.0.0.1"] = time.time() - 1             # the lockout is over
        self.assertEqual(self.run_async(hello(creds["token"]))["type"], "welcome")

    def test_pair_code_comes_with_a_qr_code(self):
        info = self.server.new_pair_code()
        self.assertIn("<svg", info["qr_svg"])
        self.assertEqual((info["host"], info["port"]), ("127.0.0.1", self.server.port))

    # ------------------------------------------------------------ commands and events

    def test_command_reply_and_events_are_mirrored(self):
        async def go():
            phone, creds = await self.paired_phone()
            ack = await phone.request(type="command", text="open notepad")
            ref = ack["result"]["ref"]
            self.controller.emit({"type": "transcript", "text": "open notepad", "source": "phone", "ref": ref})
            self.controller.emit({"type": "level", "v": 0.3})                 # not mirrored
            self.controller.emit({"type": "tool_result", "tool": "open_app", "ok": True, "content": "x" * 2000,
                                  "data": {"images": ["big"]}})
            self.controller.emit({"type": "reply", "text": "Opened Notepad. token=abcd1234secret", "source": "phone", "ref": ref})
            reply = await phone.until(lambda m: m["type"] == "event" and m["event"]["type"] == "reply")
            await phone.close()
            return creds, ack, reply
        creds, ack, reply = self.run_async(go())
        self.assertEqual(ack["result"]["result"], "sent")
        sent = self.controller.remote[0]
        self.assertEqual((sent["text"], sent["source"]), ("open notepad", "phone"))
        self.assertTrue(sent["ref"].startswith(f"ph:{creds['device_id']}:"))
        self.assertNotIn("abcd1234secret", json.dumps(reply))             # redacted before leaving the PC
        self.assertEqual(reply["event"]["ref"], ack["result"]["ref"])

    def test_tool_output_is_trimmed_and_images_dropped(self):
        async def go():
            phone, _ = await self.paired_phone()
            self.controller.emit({"type": "tool_result", "tool": "read_file", "ok": True, "content": "x" * 2000,
                                  "data": {"images": ["big"]}})
            m = await phone.until(lambda m: m["type"] == "event")
            await phone.close()
            return m["event"]
        ev = self.run_async(go())
        self.assertLessEqual(len(ev["content"]), 500)
        self.assertNotIn("data", ev)

    def test_voice_upload_is_saved_queued_and_cleaned_up(self):
        async def go():
            phone, _ = await self.paired_phone()
            ack = await phone.request(type="voice", mime="audio/mp4", data=base64.b64encode(b"\x00" * 4000).decode())
            too_big = await phone.request(type="voice", mime="audio/mp4",
                                          data=base64.b64encode(b"\x00" * (1300 * 1024)).decode())   # > 1 MB limit
            await phone.close()
            return ack, too_big
        ack, too_big = self.run_async(go())
        self.assertTrue(ack["ok"], ack)
        path = self.controller.remote[0]["audio"]
        self.assertTrue(path.endswith(".m4a"))
        self.assertTrue(Path(path).exists())
        self.controller.emit({"type": "transcript", "text": "open notepad", "source": "phone", "ref": ack["result"]["ref"]})
        self.assertFalse(Path(path).exists())
        self.assertFalse(too_big["ok"])
        self.assertEqual(len(self.controller.remote), 1)

    def test_approvals_cancel_mute_tasks(self):
        async def go():
            phone, _ = await self.paired_phone()
            self.controller.emit({"type": "approval_request", "id": 7, "tool": "forget", "title": "Allow: Delete memory?",
                                  "rows": [], "timeout_s": 180, "source": "phone"})
            req = await phone.until(lambda m: m["type"] == "event" and m["event"]["type"] == "approval_request")
            a = await phone.request(type="approve", request_id=7, allowed=True)
            c = await phone.request(type="cancel")
            mu = await phone.request(type="mute", muted=True)
            t = await phone.request(type="task", task_id=3, action="resume", answer="use main")
            bad = await phone.request(type="task", task_id=3, action="explode")
            unknown = await phone.request(type="format_c")
            await phone.close()
            return req, a, c, mu, t, bad, unknown
        req, a, c, mu, t, bad, unknown = self.run_async(go())
        self.assertEqual(req["event"]["title"], "Allow: Delete memory?")
        self.assertEqual(self.controller.approvals, [(7, True, "phone")])
        self.assertTrue(c["ok"] and self.controller.cancelled)
        self.assertTrue(mu["result"])
        self.assertEqual(self.controller.task_calls, [("resume", 3, "use main")])
        self.assertFalse(bad["ok"])
        self.assertFalse(unknown["ok"])

    def test_screenshot_history_tasks(self):
        self.memory.add_message("s", "user", "hello", source="phone")
        async def go():
            phone, _ = await self.paired_phone()
            shot = await phone.request(type="screenshot")
            hist = await phone.request(type="history", limit=10)
            tasks = await phone.request(type="tasks")
            act = await phone.request(type="activity")
            self.cfg["phone"]["allow_screenshot"] = False
            refused = await phone.request(type="screenshot")
            await phone.close()
            return shot, hist, tasks, act, refused
        shot, hist, tasks, act, refused = self.run_async(go())
        self.assertEqual(base64.b64decode(shot["result"]["data"]), b"\xff\xd8JPEG")
        self.assertEqual(hist["result"][0]["text"], "hello")
        self.assertEqual(tasks["result"], [])
        self.assertTrue(act["ok"])
        self.assertFalse(refused["ok"])
        self.assertIn("turned off", refused["error"])

    def test_revoked_phone_is_disconnected(self):
        async def go():
            phone, creds = await self.paired_phone()
            self.assertTrue(wait_for(lambda: len(self.server.conns) == 1, 2))
            await asyncio.get_running_loop().run_in_executor(None, self.server.revoke, creds["device_id"])
            m = await phone.until(lambda m: m["type"] == "_closed")
            await phone.close()
            return m, creds
        m, creds = self.run_async(go())
        self.assertEqual(m["code"], CLOSE_REVOKED)
        self.assertIsNone(self.memory.get_phone_device(creds["device_id"]))

    def test_stop_releases_the_port(self):
        port = self.server.port
        self.server.stop()
        self.assertEqual(self.controller.listeners, [])
        again = PhoneServer(self.controller, load_config=lambda: self.cfg)
        self.assertTrue(again.start(host="127.0.0.1", port=port), again.error)
        again.stop()


class BindTests(unittest.TestCase):
    def test_every_interface_is_refused(self):
        for bind in ("0.0.0.0", "::"):
            host, err = resolve_bind_host(bind)
            self.assertIsNone(host)
            self.assertIn("not allowed", err)
        self.assertEqual(resolve_bind_host("127.0.0.1"), ("127.0.0.1", None))
        self.assertIsNone(resolve_bind_host("nonsense")[0])

    def test_missing_tailscale_gives_a_clear_reason(self):
        orig = phone_server.tailscale_ip
        phone_server.tailscale_ip = lambda: None
        try:
            host, err = resolve_bind_host("tailscale")
        finally:
            phone_server.tailscale_ip = orig
        self.assertIsNone(host)
        self.assertIn("Tailscale", err)
        server = PhoneServer(StubController(None), load_config=lambda: {"phone": {"bind": "tailscale"}},
                             resolve_host=lambda b: (None, err))
        self.assertFalse(server.start())
        self.assertIn("Tailscale", server.error)

    def test_config_refuses_every_interface(self):
        from src.core.config_store import validate_update
        self.assertTrue(validate_update({"phone.bind": "0.0.0.0"}))
        self.assertEqual(validate_update({"phone.bind": "tailscale"}), [])


if __name__ == "__main__":
    unittest.main()
