"""Provider-neutral types for the tool-calling loop, and the interface every provider adapter implements.

The loop (src/agent/tool_loop_backend.py) keeps the conversation as a list of Message objects; each adapter
converts them to its wire format and its response back to a Completion. An assistant Message keeps the
provider's own content (`raw`) so the same adapter can send it back unchanged: some providers sign parts of
the reply (Anthropic thinking blocks) and reject a rebuilt copy.

Adapters never raise provider SDK exceptions: everything becomes a ProviderError with a `kind` the loop and
the UI understand (auth, rate_limit, timeout, ...).
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    error: str = ""                 # set when the model's arguments were not valid JSON


@dataclass
class ToolResultItem:
    call_id: str
    name: str
    text: str
    is_error: bool = False
    images: list = field(default_factory=list)     # [{"data": base64, "mimeType": "image/png"}]


@dataclass
class Message:
    role: str                       # user | assistant | tool_results
    text: str = ""
    tool_calls: list = field(default_factory=list)  # assistant: [ToolCall]
    results: list = field(default_factory=list)     # tool_results: [ToolResultItem]
    raw: Any = None                 # assistant: the provider's own content, replayed as is
    raw_from: str = ""              # which adapter produced `raw` (another adapter rebuilds from the fields)


@dataclass
class Usage:
    input_tokens: int = 0           # uncached input
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Everything the model read for this request: the current size of the conversation."""
        return self.input_tokens + self.cache_read + self.cache_write


@dataclass
class Completion:
    text: str
    tool_calls: list                # [ToolCall]
    stop_reason: str                # end | tool_use | max_tokens | refusal | other
    usage: Usage
    raw: Any = None
    model: str = ""


@dataclass
class Capabilities:
    tools: bool = True
    vision: bool = False
    prompt_cache: bool = False
    local: bool = False             # runs on this PC (no per-token cost, nothing leaves the machine)


class ProviderError(Exception):
    """kind: auth | permission | not_found | rate_limit | overloaded | bad_request | context_length |
    timeout | network | server | cancelled | config"""

    def __init__(self, kind: str, message: str, status: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after

    def spoken(self) -> str:
        """One plain sentence for the voice."""
        return {
            "auth": "The AI service rejected the API key. Check it in Settings.",
            "permission": "This API key isn't allowed to use that model.",
            "not_found": "The AI service doesn't know that model. Check the model name in Settings.",
            "rate_limit": "The AI service is rate limiting me right now. Try again in a minute.",
            "overloaded": "The AI service is overloaded right now. Try again in a minute.",
            "context_length": "The conversation got too long for the model. Say new conversation and try again.",
            "timeout": "The AI service took too long to answer.",
            "network": "I couldn't reach the AI service. Check the connection.",
            "config": "The AI connection isn't set up yet. Open Settings, AI.",
        }.get(self.kind, "Sorry, the AI service returned an error.")


class ProviderAdapter:
    """One wire format (Anthropic Messages, OpenAI chat completions). Subclasses implement _complete()."""

    kind = "base"

    def __init__(self, provider: str, model: str, settings: dict):
        self.provider = provider    # the config key: anthropic, openai, ollama, ...
        self.model = model
        self.settings = settings    # llm.providers.<provider>, merged with the shared llm settings
        self.capabilities = Capabilities()

    def complete(self, system: str, messages: list, tools: list, cancel: threading.Event,
                 timeout_s: float, total_s: Optional[float] = None) -> Completion:
        """One model response. timeout_s bounds each wait for the server (an idle limit when streaming);
        total_s bounds the whole call, SDK retries included. Returns as soon as `cancel` is set
        (ProviderError kind=cancelled); the request still running in the background is closed and dropped."""
        return run_cancellable(lambda holder: self._complete(system, messages, tools, timeout_s, holder),
                               cancel, total_s if total_s else max(5.0, timeout_s) * 3 + 10)

    def _complete(self, system: str, messages: list, tools: list, timeout_s: float, holder: dict) -> Completion:
        raise NotImplementedError

    def list_models(self) -> list:
        """Model ids the endpoint offers (GET /models), sorted."""
        return []

    def close(self) -> None:
        pass


def run_cancellable(fn: Callable[[dict], Any], cancel: threading.Event, limit_s: float):
    """Run fn(holder) in a worker thread; wait for it, a cancel or limit_s seconds.

    fn may put {"close": callable} into holder; it is called when we stop waiting, so an open HTTP stream is
    torn down instead of finishing in the background."""
    holder: dict = {}
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box["value"] = fn(holder)
        except BaseException as e:          # re-raised in the caller's thread
            box["error"] = e
        finally:
            done.set()

    threading.Thread(target=work, name="llm-request", daemon=True).start()
    limit = max(1.0, float(limit_s))
    started = time.monotonic()
    while not done.wait(0.1):
        if cancel.is_set() or time.monotonic() - started > limit:
            closer = holder.get("close")
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
            if cancel.is_set():
                raise ProviderError("cancelled", "Cancelled")
            raise ProviderError("timeout", f"No answer within {limit:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["value"]
