"""LIVE: a stand-in phone against the running Jarvis app (phone server on, see Settings › Phone app).

    python tests/live_phone.py                       # temporary device, removed afterwards
    python tests/live_phone.py --say "what time is it" --screenshot
    python tests/live_phone.py --host 100.76.187.123 --port 8765

It registers a temporary device straight in data/jarvis.db (pairing by code is covered by
tests/test_phone_server.py), connects over the real address, sends a command, prints the
mirrored events until the reply, optionally fetches a screenshot, and removes the device.
"""
import argparse
import asyncio
import base64
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp  # noqa: E402

from src.core import paths  # noqa: E402
from src.core.config_store import load_config  # noqa: E402
from src.memory.store import MemoryStore  # noqa: E402
from src.remote.phone_server import resolve_bind_host, token_hash  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def run(host: str, port: int, device_id: str, token: str, say: str, screenshot: bool) -> int:
    async with aiohttp.ClientSession() as session:
        t0 = time.time()
        async with session.get(f"http://{host}:{port}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
            print(f"OK: /health {await r.json()} ({(time.time() - t0) * 1000:.0f} ms)")
        async with session.ws_connect(f"http://{host}:{port}/ws") as ws:
            await ws.send_str(json.dumps({"type": "hello", "device_id": device_id, "token": token}))
            welcome = json.loads((await ws.receive(timeout=5)).data)
            if welcome.get("type") != "welcome":
                print(f"FAIL: no welcome: {welcome}")
                return 1
            st = welcome["status"]
            print(f"OK: welcome · PC {st['pc']} · phase {st['phase']} · model {st.get('model')} · dry_run {st.get('dry_run')}")
            await ws.send_str(json.dumps({"type": "command", "id": "c1", "text": say}))
            ref, end = None, time.time() + 120
            while time.time() < end:
                msg = json.loads((await ws.receive(timeout=max(1, end - time.time()))).data)
                if msg["type"] == "ack" and msg["id"] == "c1":
                    if not msg["ok"]:
                        print(f"FAIL: {msg['error']}")
                        return 1
                    ref = msg["result"]["ref"]
                    print(f"OK: command accepted ({msg['result']['result']}, ref {ref})")
                elif msg["type"] == "event":
                    ev = msg["event"]
                    brief = {k: v for k, v in ev.items() if k in ("phase", "text", "tool", "purpose", "status", "detail", "title")}
                    print(f"   event {ev['type']}: {brief}")
                    if ev.get("ref") == ref and ev["type"] in ("reply", "error", "cancelled"):
                        print("OK: reply received" if ev["type"] == "reply" else f"FAIL: {ev['type']}")
                        if ev["type"] != "reply":
                            return 1
                        break
            else:
                print("FAIL: no reply within 120 s")
                return 1
            if screenshot:
                await ws.send_str(json.dumps({"type": "screenshot", "id": "s1"}))
                while True:
                    msg = json.loads((await ws.receive(timeout=30)).data)
                    if msg["type"] == "ack" and msg["id"] == "s1":
                        break
                if not msg["ok"]:
                    print(f"FAIL: screenshot: {msg['error']}")
                    return 1
                out = paths.cache_dir() / "phone" / "live_screenshot.jpg"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(msg["result"]["data"]))
                print(f"OK: screenshot {out.stat().st_size // 1024} KB -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--say", default="What time is it? Use your clock tool.")
    ap.add_argument("--screenshot", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    phone = cfg.get("phone", {})
    host = args.host
    if not host:
        host, err = resolve_bind_host(phone.get("bind", "tailscale"))
        if not host:
            print(f"FAIL: {err}")
            return 1
    port = args.port or int(phone.get("port", 8765))
    memory = MemoryStore()
    device_id, token = "live" + uuid.uuid4().hex[:8], secrets.token_urlsafe(32)
    memory.add_phone_device(device_id, "live_phone.py", token_hash(token))
    try:
        return asyncio.run(run(host, port, device_id, token, args.say, args.screenshot))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        print(f"FAIL: cannot reach {host}:{port} ({type(e).__name__}: {e}). Is the phone app server on?")
        return 1
    finally:
        memory.delete_phone_device(device_id)
        memory.close()


if __name__ == "__main__":
    sys.exit(main())
