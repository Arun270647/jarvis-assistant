"""The error catalogue: what an error means for the user, in one plain sentence, and the button that fixes it.

explain(stage, detail, name) looks at an error event's stage and text (the pipeline, the agent backends and
the downloader report them) and returns {"code", "message", "fix": {"label", "action", "arg"}} or None when
the error isn't one we can explain better than its own text. The window shows it as a banner; actions are
handled in the web UI (app.js): open_mic_privacy, settings:<section>, claude_login, claude_install,
open_url, download, restart.
"""
import re
from typing import Optional

PRIVACY_HINTS = ("unanticipated host error", "-9999", "access is denied", "permission denied")
NO_MIC_HINTS = ("no default input device", "invalid device", "device unavailable", "error querying device",
                "invalid number of channels", "no input device", "-9996", "-9998")
OFFLINE_HINTS = ("could not connect", "connection error", "getaddrinfo", "name resolution", "network is unreachable",
                 "failed to establish a new connection", "no internet", "timed out connecting")
SIGNED_OUT_HINTS = ("not logged in", "please run /login", "logged out", "oauth token has expired",
                    "authentication_failed", "invalid api key · please run /login", "sign in again")
LIMIT_HINTS = ("usage limit", "limit reached", "rate_limit", "out of extra usage", "plan limit")
BAD_KEY_HINTS = ("api key rejected", "incorrect api key", "invalid x-api-key", "invalid api key")
CLAUDE_MISSING_HINTS = ("claude code cli not found", "claude-agent-sdk not installed")
CLAUDE_SETUP = "https://docs.claude.com/en/docs/claude-code/setup"


def _has(text: str, hints) -> bool:
    return any(h in text for h in hints)


def _reset_time(detail: str) -> str:
    m = re.search(r"reset[s]?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?(?:\s*\([^)]*\))?)", detail, re.I)
    return m.group(1).strip() if m else ""


def entry(code: str, message: str, label: str = "", action: str = "", arg: str = "") -> dict:
    return {"code": code, "message": message,
            "fix": {"label": label, "action": action, "arg": arg} if label else None}


def explain(stage: str, detail: str, name: str = "Jarvis") -> Optional[dict]:
    text = (detail or "").lower()
    if stage in ("voice", "audio"):
        if _has(text, PRIVACY_HINTS):
            return entry("mic_blocked", f"{name} can't use the microphone because it's turned off in privacy settings.",
                         "Open privacy settings", "open_mic_privacy")
        if _has(text, NO_MIC_HINTS) or "microphone read failed" in text:
            return entry("mic_missing", "Your microphone was disconnected or isn't available. Typing still works.",
                         "Choose another microphone", "settings", "microphone")
        if "model.bin" in text or "whisper" in text or "unable to open file" in text or "speech detection model" in text:
            return entry("stt_missing", "Speech recognition isn't downloaded yet, so only typing works for now.",
                         "Open Models", "settings", "models")
        return entry("voice_off", f"Voice input isn't working right now, so {name} only reads typed commands.",
                     "Open Settings", "settings", "microphone")
    if stage == "tts":
        return entry("voice_unavailable", f"{name}'s voice couldn't start, so answers are shown as text.",
                     "Open Models", "settings", "models")
    if stage == "budget":
        return entry("budget", "Today's AI spending limit is reached, so new requests wait until tomorrow.",
                     "Change the limit", "settings", "ai")
    if stage in ("llm", "config"):
        if _has(text, CLAUDE_MISSING_HINTS):
            return entry("claude_missing", "Claude needs Claude Code, Anthropic's app, which isn't installed.",
                         "Get Claude ready", "claude_install")
        if _has(text, SIGNED_OUT_HINTS) or ("claude code" in text and "logged in" in text):
            return entry("signed_out", "You're signed out of Claude.", "Sign in", "claude_login")
        if _has(text, LIMIT_HINTS) and "claude" in text:
            until = _reset_time(detail)
            return entry("limit", "Claude's usage limit is reached" + (f" until {until}." if until else " for now."),
                         "Use an API key instead", "settings", "ai")
        if _has(text, BAD_KEY_HINTS):
            return entry("bad_key", "This API key didn't work. It may be mistyped or revoked.", "Change key", "settings", "ai")
        if _has(text, OFFLINE_HINTS):
            return entry("offline", "You're offline, or the AI service can't be reached.", "Try again", "restart")
        if "no model chosen" in text or "no api key saved" in text or "no server address" in text:
            return entry("ai_setup", "The AI connection isn't set up yet.", "Open Settings › AI", "settings", "ai")
    if stage == "download":
        return entry("download_failed", "A model didn't finish downloading.", "Retry", "download")
    return None
