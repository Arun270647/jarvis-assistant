"""The utility role: short background writing jobs (greeting batches, conversation summaries).

It runs on llm.utility (by default the main provider's small, cheap model: Haiku for Claude) so it never
competes with the agent's session. generator(prompt) -> text; it raises on failure and callers fall back.
"""
import tempfile
import threading
from typing import Callable, Optional

from src.agent.providers import create_adapter, role
from src.agent.providers.base import Message

UTILITY_SYSTEM = "You write short texts for a desktop voice assistant. Follow the instructions exactly."


def claude_print(cli: str, model: str, prompt: str, timeout_s: float) -> str:
    """One answer from the Claude Code CLI in print mode (the user's Claude login)."""
    from src.core import winproc
    cmd = [cli or "claude", "-p", prompt] + (["--model", model] if model else [])
    # A neutral folder, so the CLI doesn't load a project's CLAUDE.md for a one-line job.
    res = winproc.run_hidden(cmd, timeout=timeout_s, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=tempfile.gettempdir())
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "").strip()[:300] or f"exit {res.returncode}")
    return res.stdout


def utility_generator(config: dict, meter=None, timeout_s: float = 120) -> Callable[[str], str]:
    """prompt -> text on the utility provider. Settings are read when it is built; the adapter (and its API
    key) is looked up on each call, so a key saved later is picked up."""
    provider, model, settings = role(config, "utility")
    if settings["kind"] == "claude_code":
        cli = settings.get("cli_path") or "claude"
        return lambda prompt: claude_print(cli, model, prompt, timeout_s)

    def generate(prompt: str) -> str:
        adapter = create_adapter(config, "utility")
        try:
            comp = adapter.complete(UTILITY_SYSTEM, [Message("user", text=prompt)], [], threading.Event(), timeout_s)
        finally:
            adapter.close()
        if meter is not None:
            meter.record(provider, adapter.model, comp.usage, settings, role="utility")
        return comp.text
    return generate


def describe(config: dict) -> Optional[str]:
    """"provider · model" of the utility role, for logs and Settings."""
    try:
        provider, model, _ = role(config, "utility")
        return f"{provider} · {model or 'default'}"
    except Exception:
        return None
