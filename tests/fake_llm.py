"""A fake AI provider for the adapter tests: an HTTP server on 127.0.0.1 that replays scripted responses.

It speaks just enough of both wire formats: OpenAI chat completions (POST /v1/chat/completions, JSON) and
the Anthropic Messages API (POST /v1/messages, always streamed as server-sent events), plus GET /v1/models.
Every request body is recorded, so a test can check exactly what an adapter sent.

    server = FakeLLMServer()
    server.push(openai_text("hello"))          # one scripted response per request, in order
    base_url = server.url + "/v1"
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLLMServer:
    def __init__(self):
        self.script = []                # responses still to send
        self.requests = []              # [{"method", "path", "headers", "body"}]
        self.models = ["model-a", "model-b"]
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            disable_nagle_algorithm = True      # small writes would otherwise wait ~200 ms for delayed ACKs

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer._record(self, None)
                if self.path.split("?")[0].rstrip("/").endswith("/models"):
                    body = {"object": "list", "data": [{"id": m, "object": "model", "created": 0, "owned_by": "x",
                                                        "type": "model", "display_name": m,
                                                        "created_at": "2026-01-01T00:00:00Z"} for m in outer.models],
                            "has_more": False, "first_id": None, "last_id": None}
                    return self._json(200, body)
                self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer._record(self, body)
                with outer._lock:
                    step = outer.script.pop(0) if outer.script else {"status": 500, "json": {"error": {"message": "no script"}}}
                if step.get("delay"):
                    time.sleep(step["delay"])
                if "sse" in step:
                    return self._sse(step["sse"])
                self._json(step.get("status", 200), step.get("json", {}), step.get("headers"))

            def _json(self, status, payload, headers=None):
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for k, v in (headers or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def _sse(self, events):
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for name, data in events:
                        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="fake-llm",
                         daemon=True).start()

    def _record(self, handler, body):
        with self._lock:
            self.requests.append({"method": handler.command, "path": handler.path,
                                  "headers": {k.lower(): v for k, v in handler.headers.items()}, "body": body})

    def push(self, *steps):
        with self._lock:
            self.script.extend(steps)

    def bodies(self, path_part: str = "") -> list:
        return [r["body"] for r in self.requests if r["method"] == "POST" and path_part in r["path"]]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------- OpenAI chat completions

def openai_message(content=None, tool_calls=None, finish=None, prompt_tokens=50, completion_tokens=7, cached=0):
    calls = [{"id": cid, "type": "function", "function": {"name": name, "arguments": args if isinstance(args, str)
                                                            else json.dumps(args)}}
             for cid, name, args in (tool_calls or [])]
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
             "total_tokens": prompt_tokens + completion_tokens}
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {"status": 200, "json": {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "model-a",
        "choices": [{"index": 0, "message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
        "usage": usage}}


def openai_text(text, **kw):
    return openai_message(content=text, **kw)


def openai_tools(*calls, **kw):
    """calls: (id, name, args dict or raw string)."""
    return openai_message(content=None, tool_calls=calls, **kw)


def openai_error(status, message, headers=None):
    return {"status": status, "json": {"error": {"message": message, "type": "error", "code": None}},
            "headers": headers or {}}


# ---------------------------------------------------------------------- Anthropic Messages (streamed)

def anthropic_stream(content, stop_reason="end_turn", input_tokens=40, output_tokens=6, cache_read=0, cache_write=0,
                     model="claude-test"):
    """SSE events for a complete message. content: list of blocks as the API returns them
    ({"type": "text", "text"}, {"type": "tool_use", "id", "name", "input"}, {"type": "thinking", ...})."""
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": 1, "cache_read_input_tokens": cache_read,
                  "cache_creation_input_tokens": cache_write}}})]
    for i, block in enumerate(content):
        kind = block["type"]
        if kind == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "text_delta", "text": block["text"]}}))
        elif kind == "tool_use":
            events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
        elif kind == "thinking":
            events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "thinking", "thinking": "", "signature": ""}}))
            if block.get("thinking"):
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                    "type": "thinking_delta", "thinking": block["thinking"]}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "signature_delta", "signature": block["signature"]}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                     "usage": {"output_tokens": output_tokens}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return {"sse": events}


def anthropic_text(text, **kw):
    return anthropic_stream([{"type": "text", "text": text}], **kw)


def anthropic_tools(*calls, text=None, **kw):
    """calls: (id, name, input dict)."""
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": cid, "name": name, "input": args} for cid, name, args in calls]
    return anthropic_stream(content, stop_reason="tool_use", **kw)


def anthropic_error(status, kind, message, headers=None):
    return {"status": status, "json": {"type": "error", "error": {"type": kind, "message": message}},
            "headers": headers or {}}
