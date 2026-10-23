"""OpenAI-compatible chat completions (/v1/chat/completions with tools), through the official openai SDK.

One adapter for every server that speaks this format: OpenAI, OpenRouter, Groq, Google Gemini's
OpenAI-compatible endpoint, Ollama, LM Studio, vLLM and others. Differences are settings, not code:
base_url, needs_key, vision, and `schema` ("full", or "basic" for servers that reject validation keywords
such as minLength; the policy executor validates every call against the full schema either way).

Tool results cannot carry images in this format (the tool role is text only), so images from a tool come
right after the tool messages as one user message with image parts, when the model has vision.
"""
import copy
import json
import uuid

from src.agent.providers.base import (Capabilities, Completion, Message, ProviderAdapter, ProviderError, ToolCall,
                                      Usage)

# Validation keywords some servers reject; the model doesn't need them (PolicyExecutor enforces them).
BASIC_DROP = {"minLength", "maxLength", "pattern", "format", "minimum", "maximum", "exclusiveMinimum",
              "exclusiveMaximum", "minItems", "maxItems", "uniqueItems", "additionalProperties", "default",
              "examples", "$schema"}
VISION_HINTS = ("vision", "-vl", "vl-", "llava", "gpt-4o", "gpt-4.1", "gpt-5", "gemini", "gemma3", "pixtral",
                "minicpm-v", "moondream", "qwen2.5vl", "llama4", "mistral-small3")


def sanitize_schema(schema: dict, mode: str = "full") -> dict:
    """A copy of a tool's JSON schema for the wire. "basic" drops validation keywords recursively, without
    touching property names (a property may well be called "pattern")."""
    def clean(node):
        if isinstance(node, list):
            return [clean(x) for x in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k == "$schema" or (mode == "basic" and k in BASIC_DROP):
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: clean(sub) for name, sub in v.items()}
            elif k in ("items", "anyOf", "oneOf", "allOf", "not"):
                out[k] = clean(v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    return clean(schema)


def guess_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in VISION_HINTS)


class OpenAICompatAdapter(ProviderAdapter):
    kind = "openai_compat"

    def __init__(self, provider: str, model: str, settings: dict, api_key=None):
        super().__init__(provider, model, settings)
        vision = settings.get("vision")
        self.capabilities = Capabilities(
            tools=True,
            vision=guess_vision(model) if vision is None else bool(vision),
            prompt_cache=False,                 # automatic on some servers; nothing to mark
            local=bool(settings.get("local")),
        )
        self._key = api_key
        self._client = None
        self._schema_mode = settings.get("schema", "full")

    # ------------------------------------------------------------ client

    def client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise ProviderError("config", "The openai package is not installed: pip install openai") from e
            base_url = (self.settings.get("base_url") or "").rstrip("/")
            if not base_url:
                raise ProviderError("config", f"{self.provider}: no server address (base URL) set")
            self._client = openai.OpenAI(
                api_key=self._key or "not-needed",      # local servers ignore it; the SDK insists on one
                base_url=base_url,
                max_retries=int(self.settings.get("max_retries", 2)),
                timeout=float(self.settings.get("timeout_seconds", 120)),
                default_headers=self.settings.get("headers") or None,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------ wire format

    def to_wire(self, system: str, messages: list) -> list:
        wire = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                wire.append(self._assistant_wire(m))
            elif m.role == "tool_results":
                images = []
                for r in m.results:
                    wire.append({"role": "tool", "tool_call_id": r.call_id, "content": r.text or "(no output)"})
                    images += [(r.name, img) for img in r.images]
                if images:
                    parts = [{"type": "text", "text": "Images returned by the tool calls above: "
                              + ", ".join(sorted({name for name, _ in images})) + "."}]
                    parts += [{"type": "image_url", "image_url": {
                        "url": f"data:{img.get('mimeType', 'image/png')};base64,{img['data']}"}} for _, img in images]
                    wire.append({"role": "user", "content": parts})
        return wire

    def _assistant_wire(self, m: Message) -> dict:
        # `raw` keeps the original argument strings, so a malformed call goes back exactly as the model wrote it.
        raw_args = m.raw.get("arguments", {}) if (m.raw_from == self.kind and isinstance(m.raw, dict)) else {}
        out = {"role": "assistant", "content": m.text or None}
        if m.tool_calls:
            out["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                "name": c.name, "arguments": raw_args.get(c.id, json.dumps(c.args, ensure_ascii=False))}}
                for c in m.tool_calls]
        elif not m.text:
            out["content"] = ""
        return out

    def tools_wire(self, tools: list) -> list:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": sanitize_schema(t.schema, self._schema_mode)}}
                for t in tools]

    # ------------------------------------------------------------ requests

    def _complete(self, system, messages, tools, timeout_s, holder) -> Completion:
        params = {"model": self.model, "messages": self.to_wire(system, messages), "timeout": timeout_s}
        if tools:
            params["tools"] = self.tools_wire(tools)
        if self.settings.get("max_output_tokens"):
            params["max_tokens"] = int(self.settings["max_output_tokens"])
        if self.settings.get("extra_body"):
            params["extra_body"] = self.settings["extra_body"]
        has_images = any(r.images for m in messages if m.role == "tool_results" for r in m.results)
        try:
            resp = self.client().chat.completions.create(**params)
        except Exception as e:
            raise self._error(e, has_images) from e
        return self.parse(resp)

    def parse(self, resp) -> Completion:
        if not getattr(resp, "choices", None):
            raise ProviderError("server", "The server returned no answer (empty choices)")
        choice = resp.choices[0]
        msg = choice.message
        calls, raw_args = [], {}
        for i, tc in enumerate(getattr(msg, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            cid = getattr(tc, "id", None) or f"call_{i}_{uuid.uuid4().hex[:8]}"
            arguments = fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments or {})
            raw_args[cid] = arguments or "{}"
            call = ToolCall(id=cid, name=fn.name or "", args={})
            try:
                parsed = json.loads(arguments) if arguments and arguments.strip() else {}
                if isinstance(parsed, dict):
                    call.args = parsed
                else:
                    call.error = "the arguments must be a JSON object"
            except ValueError as e:
                call.error = f"the arguments were not valid JSON ({e})"
            calls.append(call)
        finish = getattr(choice, "finish_reason", None) or ""
        stop = ("tool_use" if calls else
                {"stop": "end", "length": "max_tokens", "content_filter": "refusal"}.get(finish, "end" if not finish else "other"))
        u = getattr(resp, "usage", None)
        prompt = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
        details = getattr(u, "prompt_tokens_details", None) if u else None
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        usage = Usage(input_tokens=max(0, prompt - cached), cache_read=cached,
                      output_tokens=int(getattr(u, "completion_tokens", 0) or 0) if u else 0)
        return Completion(text=(msg.content or "").strip(), tool_calls=calls, stop_reason=stop, usage=usage,
                          raw={"arguments": raw_args}, model=getattr(resp, "model", "") or self.model)

    def _error(self, e: Exception, has_images: bool = False) -> ProviderError:
        try:
            import openai
        except ImportError:
            return ProviderError("server", str(e))
        status = getattr(e, "status_code", None)
        text = _message(e)
        if isinstance(e, openai.APITimeoutError):
            return ProviderError("timeout", "The AI service did not answer in time")
        if isinstance(e, openai.APIConnectionError):
            return ProviderError("network", f"Could not connect to {self.settings.get('base_url')}: {text}")
        if isinstance(e, openai.AuthenticationError):
            return ProviderError("auth", f"API key rejected: {text}", status)
        if isinstance(e, openai.PermissionDeniedError):
            return ProviderError("permission", text, status)
        if isinstance(e, openai.NotFoundError):
            return ProviderError("not_found", f"Model or endpoint not found ({self.model}): {text}", status)
        if isinstance(e, openai.RateLimitError):
            return ProviderError("rate_limit", text, status, _retry_after(e))
        if isinstance(e, (openai.BadRequestError, openai.UnprocessableEntityError)):
            low = text.lower()
            if "context" in low and ("length" in low or "window" in low or "too long" in low or "maximum" in low):
                return ProviderError("context_length", text, status)
            if has_images and "image" in low:
                return ProviderError("no_vision", text, status)
            return ProviderError("bad_request", text, status)
        if isinstance(e, openai.APIStatusError):
            return ProviderError("overloaded" if status in (502, 503, 529) else "server", text, status)
        return ProviderError("server", text)

    def list_models(self) -> list:
        try:
            return sorted({m.id for m in self.client().models.list(timeout=20)})
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
        if isinstance(err, str):
            return err[:500]
    return (getattr(e, "message", None) or str(e) or type(e).__name__)[:500]


def _retry_after(e: Exception):
    try:
        value = e.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except Exception:
        return None
