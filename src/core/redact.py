"""Keep credentials out of prompts, logs and memory.

redact() masks things that look like secrets before text is sent to Claude,
written to the log, stored in SQLite or shown in the UI. looks_secret() is used
to refuse saving such text as a memory at all.
"""
import re


def _prefixed(prefix: str, length: int = 20) -> re.Pattern:
    """A key that starts with `prefix`, not glued to a word before it, with at least one digit in it (so a
    word like "task-management-overview-page" is left alone)."""
    return re.compile(rf"(?<![A-Za-z0-9_\-]){prefix}(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{{{length},}}")


_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),                  # Anthropic keys
    _prefixed("sk-"),                                            # OpenAI (sk-proj-, sk-svcacct-, sk-admin-),
                                                                 # OpenRouter (sk-or-v1-), DeepSeek ...
    _prefixed("gsk_"),                                           # Groq
    _prefixed("xai-"),                                           # xAI
    _prefixed("hf_"),                                            # Hugging Face
    _prefixed("pplx-"),                                          # Perplexity
    _prefixed("csk-"),                                           # Cerebras
    _prefixed("r8_"),                                            # Replicate
    _prefixed("nvapi-"),                                         # NVIDIA
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                   # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),                # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                             # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),                      # Google API key
    re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_\-]{30,}"),           # Telegram-style bot tokens (also inside URLs)
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),   # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
# key=value / key: value where the key name says it is secret
_ASSIGN = re.compile(r"(?i)\b([A-Za-z0-9_\-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
                     r"[A-Za-z0-9_\-]*)(\s*[:=]\s*)(['\"]?)([^\s'\"]{4,})(\3)")
# HTTP credentials: Authorization headers, bare bearer tokens, user:password@ in URLs
_AUTH = re.compile(r"(?i)\b(authorization\s*[:=]\s*['\"]?(?:bearer|basic|token|bot)?\s*)[^\s'\"]{8,}")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}=*")
_URL_PASSWORD = re.compile(r"(://[^/\s:@]+:)[^/\s@]+(@)")

MASK = "[REDACTED]"


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub(MASK, out)
    out = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(5)}", out)
    out = _AUTH.sub(lambda m: m.group(1) + MASK, out)
    out = _BEARER.sub(lambda m: m.group(1) + MASK, out)
    out = _URL_PASSWORD.sub(lambda m: m.group(1) + MASK + m.group(2), out)
    return out


def looks_secret(text: str) -> bool:
    return redact(text) != text
