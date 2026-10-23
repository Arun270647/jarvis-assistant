"""The phone app's connection to Jarvis: a WebSocket server on the private Tailscale address.

The Jarvis Android app (mobile/) connects to ws://<tailscale-ip>:<phone.port>/ws.
Tailscale (WireGuard) encrypts the link and only the user's own devices can reach
the address; on top of that every connection must authenticate with a device token.

- Pairing: Settings > Phone app shows a QR code with the address and a 6-digit code
  (10 minutes, 5 tries). The app sends {type: pair, code, device_name} and gets a
  device id + token; only the token's SHA-256 is stored (memory table phone_devices).
- Every later connection starts with {type: hello, device_id, token} within 5 s.
- Commands and voice recordings become controller.send_remote(source="phone", ref=...):
  the pipeline runs them like typed commands, keeps the PC silent and tags the reply
  events with the ref. Voice is transcribed by the pipeline's own Whisper.
- Every controller event the app needs is mirrored to all connected phones, so the app
  is a live remote of Jarvis (desk commands included; approvals can be answered there).

aiohttp runs on its own asyncio loop in the thread jarvis-phone-server. The controller
listener only schedules work on that loop, so it never blocks the pipeline thread.
Protocol: mobile/src/api/protocol.ts mirrors the message shapes used here.
"""
import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
import uuid
from typing import Callable, Optional

from src.core import config_store, paths, winproc
from src.core.redact import redact

UPLOAD_DIR = paths.cache_dir() / "phone"          # voice notes, deleted after transcription
PROTOCOL_VERSION = 1
PAIR_TTL_S = 600
PAIR_MAX_TRIES = 5
HELLO_TIMEOUT_S = 5.0
CLOSE_UNAUTHORIZED = 4401
CLOSE_REVOKED = 4403
SCREENSHOT_MAX_SIDE = 1920
MIRRORED = {"phase", "transcript", "reply", "error", "cancelled", "info", "approval_request", "approval_resolved",
            "tool_call", "tool_result", "task_update", "task_progress", "ready", "stopped", "mute", "timing"}
AUDIO_EXT = {"audio/mp4": ".m4a", "audio/m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg",
             "audio/webm": ".webm", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/3gpp": ".3gp"}
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_EXE = r"C:\Program Files\Tailscale\tailscale.exe"


def log(msg: str) -> None:
    print(f"[PHONE] {redact(msg)}")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def scrub(obj):
    """Redact secrets in every string of an event before it leaves the PC."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj


def slim_event(ev: dict) -> dict:
    """What the phone needs from a controller event (no images, short tool output)."""
    ev = dict(ev)
    if ev.get("type") == "tool_result":
        ev.pop("data", None)
        content = str(ev.get("content") or "")
        ev["content"] = content if len(content) <= 500 else content[:499] + "…"
    return scrub(ev)


# ---------------------------------------------------------------- where to listen

def tailscale_ip() -> Optional[str]:
    exe = TAILSCALE_EXE if os.path.exists(TAILSCALE_EXE) else "tailscale"
    try:
        r = winproc.run_hidden([exe, "ip", "-4"], capture_output=True, text=True, timeout=8)
        for line in (r.stdout or "").split():
            if _in_tailnet(line):
                return line.strip()
    except (OSError, ValueError, winproc.subprocess.TimeoutExpired):
        pass
    for ip in _local_ipv4s():            # Tailscale running but the CLI missing from PATH
        if _in_tailnet(ip):
            return ip
    return None


def _in_tailnet(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.strip()) in TAILSCALE_NET
    except ValueError:
        return False


def _local_ipv4s() -> list:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        return []


def lan_ip() -> Optional[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))        # no packet is sent; picks the LAN interface
            ip = s.getsockname()[0]
        return ip if ipaddress.ip_address(ip).is_private and not _in_tailnet(ip) else None
    except OSError:
        return None


def resolve_bind_host(bind: str) -> tuple:
    """(host, None) or (None, reason). Never every interface."""
    bind = (bind or "tailscale").strip()
    if bind == "tailscale":
        ip = tailscale_ip()
        return (ip, None) if ip else (None, "Tailscale is not installed or not signed in on this PC.")
    if bind == "lan":
        ip = lan_ip()
        return (ip, None) if ip else (None, "No home-network address found.")
    try:
        addr = ipaddress.ip_address(bind)
    except ValueError:
        return None, f"phone.bind must be tailscale, lan or an IP address (got {bind!r})."
    if addr.is_unspecified:
        return None, "Listening on every interface is not allowed."
    return str(addr), None


def screenshot_jpeg() -> bytes:
    """All monitors, longest side SCREENSHOT_MAX_SIDE, as JPEG."""
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True).convert("RGB")
    scale = min(1.0, SCREENSHOT_MAX_SIDE / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def qr_svg(payload: str) -> str:
    import segno
    # omitsize: a viewBox instead of fixed width/height, so the page can scale it without cropping;
    # border 4 is the quiet zone scanners need.
    return segno.make(payload, error="m").svg_inline(dark="#000000", light="#ffffff", border=4, omitsize=True)


# ---------------------------------------------------------------- the server

class _Conn:
    def __init__(self, ws, device_id: str, name: str):
        self.ws = ws
        self.device_id = device_id
        self.name = name
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1000)


class PhoneServer:
    def __init__(self, controller, memory=None,
                 load_config: Callable[[], dict] = config_store.load_config,
                 on_change: Optional[Callable[[], None]] = None,
                 screenshot: Callable[[], bytes] = screenshot_jpeg,
                 resolve_host: Callable[[str], tuple] = resolve_bind_host):
        self.controller = controller
        self.memory = memory if memory is not None else getattr(controller, "memory", None)
        self._load_config = load_config
        self.on_change = on_change or (lambda: None)
        self._screenshot = screenshot
        self._resolve_host = resolve_host
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._runner = None
        self._started = threading.Event()
        self.host: Optional[str] = None
        self.port: int = 0
        self.error: Optional[str] = None
        self.conns: set = set()
        self.pair: Optional[dict] = None
        self.voice_files: dict = {}          # ref -> path, deleted once transcribed

    # ------------------------------------------------------------ lifecycle

    @property
    def cfg(self) -> dict:
        return self._load_config().get("phone", {}) or {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._started.is_set() and not self.error

    def start(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Start listening. host/port override the config (tests use 127.0.0.1 and port 0)."""
        cfg = self.cfg
        if host is None:
            host, err = self._resolve_host(cfg.get("bind", "tailscale"))
            if host is None:
                self.error = err
                log(f"not started: {err}")
                return False
        self.host, self.port, self.error = host, int(cfg.get("port", 8765) if port is None else port), None
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-phone-server", daemon=True)
        self._thread.start()
        self._started.wait(10)
        if self.error or not self._started.is_set():
            self.error = self.error or "The phone server did not start."
            log(f"not started: {self.error}")
            return False
        self.controller.add_listener(self.on_event)
        log(f"listening on {self.host}:{self.port}")
        return True

    def _run(self) -> None:
        from aiohttp import web
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        app = web.Application(client_max_size=self._max_bytes() + 1024 * 1024)
        app.router.add_get("/health", self._health)
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app, access_log=None)
        try:
            self.loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, self.host, self.port)
            self.loop.run_until_complete(site.start())
            if self.port == 0:
                self.port = site._server.sockets[0].getsockname()[1]
        except OSError as e:
            self.error = f"Could not listen on {self.host}:{self.port}: {e.strerror or e}"
            self._started.set()
            self.loop.close()
            return
        self._started.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self._runner.cleanup())
            self.loop.close()

    def stop(self, wait: float = 5.0) -> None:
        self.controller.remove_listener(self.on_event)
        loop = self.loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_all(1001, "Jarvis is shutting down"), loop).result(wait)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(wait)
        log("stopped")

    async def _close_all(self, code: int, message: str) -> None:
        for conn in list(self.conns):
            await conn.ws.close(code=code, message=message.encode())

    def _max_bytes(self) -> int:
        return int(float(self.cfg.get("max_upload_mb", 10)) * 1024 * 1024)

    # ------------------------------------------------------------ pairing / devices (called from the UI thread)

    def new_pair_code(self) -> dict:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        self.pair = {"code": code, "expires": time.time() + PAIR_TTL_S, "tries": 0}
        pc = os.environ.get("COMPUTERNAME", "PC")
        payload = json.dumps({"v": PROTOCOL_VERSION, "app": "jarvis", "host": self.host, "port": self.port,
                              "code": code, "pc": pc}, separators=(",", ":"))
        try:
            svg = qr_svg(payload)
        except Exception as e:
            log(f"QR code unavailable: {e}")
            svg = ""
        return {"code": code, "host": self.host, "port": self.port, "pc": pc, "expires_s": PAIR_TTL_S, "qr_svg": svg}

    def devices(self) -> list:
        if self.memory is None:
            return []
        online = {c.device_id for c in self.conns}
        return [dict(d, online=d["id"] in online) for d in self.memory.list_phone_devices()]

    def revoke(self, device_id: str) -> bool:
        ok = bool(self.memory and self.memory.delete_phone_device(device_id))
        loop = self.loop
        if loop is not None and loop.is_running():
            async def close():
                for conn in [c for c in self.conns if c.device_id == device_id]:
                    await conn.ws.close(code=CLOSE_REVOKED, message=b"This phone was removed in Jarvis's settings")
            asyncio.run_coroutine_threadsafe(close(), loop)
        if ok:
            log(f"removed device {device_id}")
        return ok

    def status(self) -> dict:
        return {"running": self.running, "host": self.host, "port": self.port, "error": self.error,
                "devices": self.devices(), "connected": len(self.conns),
                "pair_code": self.pair["code"] if self.pair and self.pair["expires"] > time.time() else None}

    # ------------------------------------------------------------ events (pipeline thread -> phones)

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        ref = ev.get("ref")
        if ref and ref in self.voice_files and (t == "transcript" or (t == "error" and ev.get("stage") == "stt")):
            path = self.voice_files.pop(ref)
            try:
                os.remove(path)
            except OSError:
                pass
        if t not in MIRRORED or not self.conns:
            return
        loop = self.loop
        if loop is None or not loop.is_running():
            return
        payload = {"type": "event", "event": slim_event(ev)}
        loop.call_soon_threadsafe(self._broadcast, payload)

    def _broadcast(self, payload: dict) -> None:
        for conn in list(self.conns):
            try:
                conn.queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass                    # a stalled phone misses events; it resyncs on reconnect

    # ------------------------------------------------------------ HTTP / WebSocket

    async def _health(self, request):
        from aiohttp import web
        return web.json_response({"name": "jarvis", "version": PROTOCOL_VERSION})

    async def _ws(self, request):
        from aiohttp import WSMsgType, web
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=self._max_bytes() * 2)
        await ws.prepare(request)
        conn = await self._authenticate(ws)
        if conn is None:
            return ws
        self.conns.add(conn)
        self._changed()
        sender = asyncio.ensure_future(self._sender(conn))
        try:
            await self._send(ws, await self._welcome(conn))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._dispatch(conn, msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            sender.cancel()
            self.conns.discard(conn)
            self._changed()
            log(f"{conn.name} disconnected")
        return ws

    def _changed(self) -> None:
        try:
            self.on_change()
        except Exception:
            pass

    async def _sender(self, conn: _Conn) -> None:
        while True:
            payload = await conn.queue.get()
            if conn.ws.closed:
                return
            await self._send(conn.ws, payload)

    @staticmethod
    async def _send(ws, payload: dict) -> None:
        try:
            await ws.send_str(json.dumps(payload, default=str))
        except (ConnectionError, RuntimeError):
            pass

    async def _authenticate(self, ws) -> Optional[_Conn]:
        from aiohttp import WSMsgType
        try:
            msg = await asyncio.wait_for(ws.receive(), HELLO_TIMEOUT_S)
            data = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
        except (asyncio.TimeoutError, ValueError, TypeError):
            data = {}
        kind = data.get("type")
        if kind == "pair":
            return await self._pair(ws, data)
        if kind == "hello" and self.memory is not None:
            dev = await self._db(self.memory.get_phone_device, str(data.get("device_id", "")))
            token = str(data.get("token", ""))
            if dev and token and secrets.compare_digest(token_hash(token), dev["token_sha256"]):
                await self._db(self.memory.touch_phone_device, dev["id"])
                log(f"{dev['name']} connected")
                return _Conn(ws, dev["id"], dev["name"])
        await ws.close(code=CLOSE_UNAUTHORIZED, message=b"Not paired")
        return None

    async def _pair(self, ws, data: dict) -> Optional[_Conn]:
        pair = self.pair
        code = str(data.get("code", ""))
        ok = (pair is not None and time.time() <= pair["expires"] and self.memory is not None
              and secrets.compare_digest(code, pair["code"]))
        if not ok:
            if pair is not None:
                pair["tries"] += 1
                if pair["tries"] >= PAIR_MAX_TRIES:
                    self.pair = None
                    log("pairing closed after too many wrong codes")
            await self._send(ws, {"type": "ack", "id": data.get("id"), "ok": False,
                                  "error": "That code is not valid or has expired."})
            await ws.close(code=CLOSE_UNAUTHORIZED, message=b"Pairing failed")
            return None
        self.pair = None
        name = re.sub(r"[^\w .\-()']", "", str(data.get("device_name") or "Phone"))[:60] or "Phone"
        device_id, token = uuid.uuid4().hex[:12], secrets.token_urlsafe(32)
        await self._db(self.memory.add_phone_device, device_id, name, token_hash(token))
        log(f"paired {name} ({device_id})")
        await self._send(ws, {"type": "ack", "id": data.get("id"), "ok": True,
                              "result": {"device_id": device_id, "token": token,
                                         "pc": os.environ.get("COMPUTERNAME", "PC")}})
        return _Conn(ws, device_id, name)

    async def _welcome(self, conn: _Conn) -> dict:
        c = self.controller
        status = c.status()
        pending = c.broker.pending if getattr(c, "broker", None) else None
        task = None
        if status.get("active_task_id") and self.memory is not None:
            task = await self._db(self.memory.get_task, status["active_task_id"])
        return scrub({"type": "welcome", "version": PROTOCOL_VERSION, "device": {"id": conn.device_id, "name": conn.name},
                      "status": status, "active_task": task,
                      "pending_approval": {"type": "approval_request", **pending.to_event()} if pending else None})

    async def _db(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    # ------------------------------------------------------------ requests from the phone

    async def _dispatch(self, conn: _Conn, raw: str) -> None:
        data = {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
                raise ValueError("expected a JSON object")
            kind = data.get("type")
            handler = getattr(self, f"_on_{kind}", None) if isinstance(kind, str) and kind.isidentifier() else None
            if handler is None:
                raise ValueError(f"unknown message type {kind!r}")
            reply = {"type": "ack", "id": data.get("id"), "ok": True, "result": await handler(conn, data)}
        except Exception as e:
            reply = {"type": "ack", "id": data.get("id"), "ok": False, "error": redact(str(e) or type(e).__name__)}
        await self._send(conn.ws, reply)

    def _ref(self, conn: _Conn, data: dict) -> str:
        return f"ph:{conn.device_id}:{str(data.get('id') or uuid.uuid4().hex[:8])[:40]}"

    async def _on_ping(self, conn, data):
        return "pong"

    async def _on_command(self, conn, data):
        text = str(data.get("text") or "").strip()
        if not text:
            raise ValueError("empty command")
        if len(text) > 4000:
            raise ValueError("command too long")
        ref = self._ref(conn, data)
        log(f"{conn.name}: command ({len(text)} chars)")
        return {"ref": ref, "result": self.controller.send_remote(text=text, source="phone", ref=ref)}

    async def _on_voice(self, conn, data):
        try:
            audio = base64.b64decode(str(data.get("data") or ""), validate=True)
        except ValueError:
            raise ValueError("voice data is not valid base64") from None
        if not audio:
            raise ValueError("empty recording")
        if len(audio) > self._max_bytes():
            raise ValueError(f"recording larger than {self.cfg.get('max_upload_mb', 10)} MB")
        ref = self._ref(conn, data)
        ext = AUDIO_EXT.get(str(data.get("mime") or "").lower(), ".m4a")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = UPLOAD_DIR / (re.sub(r"[^\w\-]", "_", ref) + ext)
        path.write_bytes(audio)
        self.voice_files[ref] = str(path)
        result = self.controller.send_remote(audio_path=str(path), source="phone", ref=ref)
        if result not in ("sent", "queued"):
            self.voice_files.pop(ref, None)
            path.unlink(missing_ok=True)
        log(f"{conn.name}: voice recording ({len(audio) // 1024} KB) -> {result}")
        return {"ref": ref, "result": result}

    async def _on_approve(self, conn, data):
        return self.controller.approve(int(data["request_id"]), bool(data.get("allowed")), via="phone")

    async def _on_cancel(self, conn, data):
        return self.controller.cancel()

    async def _on_mute(self, conn, data):
        self.controller.set_muted(bool(data.get("muted")))
        return self.controller.muted

    async def _on_start(self, conn, data):
        return await self._db(self.controller.start)

    async def _on_stop(self, conn, data):
        return await self._db(self.controller.stop)

    async def _on_status(self, conn, data):
        return self.controller.status()

    async def _on_screenshot(self, conn, data):
        if not self.cfg.get("allow_screenshot", True):
            raise PermissionError("Screenshots are turned off in Jarvis's settings.")
        jpeg = await self._db(self._screenshot)
        return {"mime": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii"), "taken_at": time.time()}

    async def _on_history(self, conn, data):
        limit = max(1, min(200, int(data.get("limit", 60))))
        return scrub(await self._db(self.memory.recent_messages, limit))

    async def _on_tasks(self, conn, data):
        return scrub(await self._db(lambda: self.memory.list_tasks(limit=50)))

    async def _on_activity(self, conn, data):
        rows = await self._db(self.memory.recent_tool_runs, 50)
        for r in rows:
            r.pop("args_hash", None)
            res = str(r.get("result") or "")
            r["result"] = res if len(res) <= 300 else res[:299] + "…"
        return scrub(rows)

    async def _on_task(self, conn, data):
        tid, action = int(data["task_id"]), str(data.get("action"))
        c = self.controller
        fn = {"pause": c.pause_task, "cancel": c.cancel_task, "retry": c.retry_task}.get(action)
        if action == "resume":
            return await self._db(c.resume_task, tid, str(data.get("answer") or ""))
        if fn is None:
            raise ValueError(f"unknown task action {action!r}")
        return await self._db(fn, tid)
