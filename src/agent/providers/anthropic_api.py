"""Anthropic Messages API (API key), through the official anthropic SDK.

- Streams every response, so `timeout_seconds` is a limit on silence rather than on the whole answer, and
  a cancel closes the connection.
- Prompt caching: an explicit breakpoint on the system prompt (tools render before it, so tools + system are
  cached together) plus automatic caching of the growing conversation.
- The assistant's content goes back exactly as received (`raw`): thinking blocks are signed and bound to
  the conversation, which the tool loop keeps append-only.
- Effort comes from llm.providers.anthropic.effort; a model that rejects it is retried without.
- For models with server-side refusal fallback on the Claude API, a declined request is re-run on
  Anthropic's recommended fallback model (fallbacks: "default").
"""
from src.agent.providers.base import Capabilities, Completion, ProviderAdapter, ProviderError, ToolCall, Usage

OFFICIAL_HOST = "api.anthropic.com"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


def _type(block) -> str:
    return block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")


def replayable(content: list) -> list:
    """The assistant content to send back: everything, except model-internal blocks from a declined partial
    answer that come before the last `fallback` block."""
    content = list(content or [])
    last = max((i for i, b in enumerate(content) if _type(b) == "fallback"), default=-1)
    if last < 0:
        return content
    return [b for i, b in enumerate(content) if i > last or _type(b) in ("text", "fallback")]


def answer_blocks(content: list) -> list:
    """The blocks that make up the final answer (after the last `fallback` block, if any)."""
    content = list(content or [])
    last = max((i for i, b in enumerate(content) if _type(b) == "fallback"), default=-1)
    return content[last + 1:]


class AnthropicAdapter(ProviderAdapter):
    kind = "anthropic"

    def __init__(self, provider: str, model: str, settings: dict, api_key=None):
        super().__init__(provider, model, settings)
        self.capabilities = Capabilities(tools=True, vision=True, prompt_cache=True, local=False)
        self._key = api_key
        self._client = None
        self._effort = str(settings.get("effort") or "")
        base = settings.get("base_url") or ""
        self._fallbacks = (settings.get("refusal_fallback", True) and model in FALLBACK_MODELS
                           and (not base or OFFICIAL_HOST in base))

    # ------------------------------------------------------------ client

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderError("config", "The anthropic package is not installed: pip install anthropic") from e
            kwargs = {"max_retries": int(self.settings.get("max_retries", 2)),
                      "timeout": float(self.settings.get("timeout_seconds", 120))}
            if self._key:
                kwargs["api_key"] = self._key
            if self.settings.get("base_url"):
                kwargs["base_url"] = self.settings["base_url"].rstrip("/")
            try:
                self._client = anthropic.Anthropic(**kwargs)
            except Exception as e:
                raise ProviderError("config", f"Could not set up the Anthropic client: {e}") from e
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------ wire format

    def to_wire(self, messages: list) -> list:
        wire = []
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.text or "(empty)"})
            elif m.role == "assistant":
                if m.raw_from == self.kind and m.raw:
                    content = replayable(m.raw)
                else:
                    content = ([{"type": "text", "text": m.text}] if m.text else []) + [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m.tool_calls]
                wire.append({"role": "assistant", "content": content or [{"type": "text", "text": "(no reply)"}]})
            elif m.role == "tool_results":
                blocks = []
                for r in m.results:
                    content = [{"type": "text", "text": r.text or "(no output)"}]
                    content += [{"type": "image", "source": {"type": "base64", "media_type": img.get("mimeType", "image/png"),
                                                             "data": img["data"]}} for img in r.images]
                    block = {"type": "tool_result", "tool_use_id": r.call_id, "content": content}
                    if r.is_error:
                        block["is_error"] = True
                    blocks.append(block)
                wire.append({"role": "user", "content": blocks})
        return wire

    def request(self, system: str, messages: list, tools: list) -> dict:
        params = {"model": self.model, "max_tokens": int(self.settings.get("max_tokens", 16000)),
                  "messages": self.to_wire(messages)}
        cache = self.settings.get("prompt_cache", True)
        if system:
            block = {"type": "text", "text": system}
            if cache:
                block["cache_control"] = {"type": "ephemeral"}
            params["system"] = [block]
        if cache:
            params["cache_control"] = {"type": "ephemeral"}      # the conversation tail
        if tools:
            params["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]
        if self._effort:
            params["output_config"] = {"effort": self._effort}
        if self._fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    # ------------------------------------------------------------ requests

    def _complete(self, system, messages, tools, timeout_s, holder) -> Completion:
        params = self.request(system, messages, tools)
        try:
            return self._stream(params, timeout_s, holder)
        except ProviderError as e:
            if e.kind == "bad_request" and self._effort and "effort" in str(e).lower():
                print(f"[LLM] {self.model} does not take effort={self._effort}; continuing without it")
                self._effort = ""
                params.pop("output_config", None)
                return self._stream(params, timeout_s, holder)
            raise

    def _stream(self, params: dict, timeout_s: float, holder: dict) -> Completion:
        client = self.client()
        api = client.beta.messages if "betas" in params else client.messages
        try:
            with api.stream(timeout=float(timeout_s), **params) as stream:
                holder["close"] = stream.close
                for _ in stream:
                    pass
                message = stream.get_final_message()
        except ProviderError:
            raise
        except Exception as e:
            raise self._error(e) from e
        return self.parse(message)

    def parse(self, message) -> Completion:
        content = list(getattr(message, "content", None) or [])
        texts, calls = [], []
        for block in answer_blocks(content):
            kind = _type(block)
            if kind == "text":
                t = (getattr(block, "text", "") or "").strip()
                if t:
                    texts.append(t)
            elif kind == "tool_use":
                args = getattr(block, "input", None)
                call = ToolCall(id=block.id, name=block.name, args=args if isinstance(args, dict) else {})
                if not isinstance(args, dict):
                    call.error = "the arguments must be a JSON object"
                calls.append(call)
        reason = getattr(message, "stop_reason", "") or ""
        stop = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use", "max_tokens": "max_tokens",
                "refusal": "refusal", "model_context_window_exceeded": "max_tokens"}.get(reason, "other")
        if calls and stop not in ("refusal", "max_tokens"):
            stop = "tool_use"
        u = getattr(message, "usage", None)
        usage = Usage(input_tokens=int(getattr(u, "input_tokens", 0) or 0),
                      output_tokens=int(getattr(u, "output_tokens", 0) or 0),
                      cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0),
                      cache_write=int(getattr(u, "cache_creation_input_tokens", 0) or 0)) if u else Usage()
        return Completion(text="\n".join(texts), tool_calls=calls, stop_reason=stop, usage=usage, raw=content,
                          model=getattr(message, "model", "") or self.model)

    def _error(self, e: Exception) -> ProviderError:
        try:
            import anthropic
        except ImportError:
            return ProviderError("server", str(e))
        status = getattr(e, "status_code", None)
        text = _message(e)
        low = text.lower()
        if isinstance(e, getattr(anthropic, "CredentialsError", ())):
            return ProviderError("auth", f"No usable API key: {text}")
        if isinstance(e, (anthropic.APITimeoutError, getattr(anthropic, "DeadlineExceededError", anthropic.APITimeoutError))):
            return ProviderError("timeout", "The Anthropic API did not answer in time")
        if isinstance(e, anthropic.APIConnectionError):
            return ProviderError("network", f"Could not connect to the Anthropic API: {text}")
        if isinstance(e, anthropic.AuthenticationError):
            return ProviderError("auth", f"API key rejected: {text}", status)
        if isinstance(e, anthropic.PermissionDeniedError):
            return ProviderError("permission", text, status)
        if isinstance(e, anthropic.NotFoundError):
            return ProviderError("not_found", f"Model not found ({self.model}): {text}", status)
        if isinstance(e, anthropic.RateLimitError):
            return ProviderError("rate_limit", text, status, _retry_after(e))
        if isinstance(e, getattr(anthropic, "RequestTooLargeError", ())):
            return ProviderError("context_length", text, status)
        if isinstance(e, anthropic.BadRequestError):
            if "prompt is too long" in low or "context window" in low or "too many tokens" in low:
                return ProviderError("context_length", text, status)
            return ProviderError("bad_request", text, status)
        if isinstance(e, anthropic.APIStatusError):
            overloaded = status in (503, 529) or "overloaded" in low
            return ProviderError("overloaded" if overloaded else "server", text, status)
        return ProviderError("server", text)

    def list_models(self) -> list:
        try:
            return sorted({m.id for m in self.client().models.list(limit=100, timeout=20)})
        except ProviderError:
            raise
        except Exception as e:
            raise self._error(e) from e


def _message(e: Exception) -> str:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
    return (getattr(e, "message", None) or str(e) or type(e).__name__)[:500]


def _retry_after(e: Exception):
    try:
        value = e.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except Exception:
        return None
