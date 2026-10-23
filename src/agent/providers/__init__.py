"""Which AI provider and model each role uses, where the API keys live, and building the adapter.

Settings (config/defaults.json, section "llm"):
  llm.main.provider            the agent's brain: a key of llm.providers
  llm.utility.provider/model   greetings and conversation summaries ("" = same provider as main, its
                               utility_model)
  llm.providers.<name>         kind (claude_code | anthropic | openai_compat), base_url, model,
                               utility_model, needs_key, env_key, local, vision, ...
Shared request settings (timeout_seconds, max_tokens, history_budget_tokens) live in "llm" and a provider
entry may override them.

API keys are never stored in settings: they go to the OS secret store (Windows Credential Manager) under
JARVIS/LLM/<provider>. The provider's environment variable (env_key, e.g. OPENAI_API_KEY) is accepted as a
fallback, for developers.
"""
import os
from typing import Optional, Tuple

from src.agent.providers.base import ProviderAdapter, ProviderError

KINDS = ("claude_code", "anthropic", "openai_compat")
SHARED_KEYS = ("timeout_seconds", "max_tokens", "history_budget_tokens")
KEY_USER = "api-key"


def providers(config: dict) -> dict:
    return (config.get("llm", {}) or {}).get("providers", {}) or {}


def settings_for(config: dict, provider: str) -> dict:
    """The provider's entry with the shared llm settings filled in. Raises ProviderError(config) if unknown."""
    llm = config.get("llm", {}) or {}
    entry = providers(config).get(provider)
    if not isinstance(entry, dict):
        raise ProviderError("config", f"Unknown AI provider: {provider or '(none)'}")
    merged = {k: llm[k] for k in SHARED_KEYS if k in llm}
    merged.update(entry)
    merged["name"] = provider
    if merged.get("kind") not in KINDS:
        raise ProviderError("config", f"{provider}: kind must be one of {', '.join(KINDS)}")
    return merged


def role(config: dict, which: str = "main") -> Tuple[str, str, dict]:
    """(provider, model, settings) for the main agent or the utility role."""
    llm = config.get("llm", {}) or {}
    main = (llm.get("main") or {}).get("provider") or "claude_code"
    if which == "main":
        s = settings_for(config, main)
        return main, str(s.get("model") or ""), s
    util = llm.get("utility") or {}
    provider = util.get("provider") or main
    s = settings_for(config, provider)
    model = util.get("model") or s.get("utility_model") or s.get("model") or ""
    return provider, str(model), s


def credential_target(provider: str) -> str:
    return f"JARVIS/LLM/{provider}"


def api_key(provider: str, settings: dict) -> Tuple[Optional[str], Optional[str]]:
    """(key, source): source is "saved" (secret store), "env:<NAME>" or None when there is no key."""
    from src.core import credentials
    key = credentials.read(credential_target(provider))
    if key:
        return key, "saved"
    env = settings.get("env_key")
    if env and os.environ.get(env):
        return os.environ[env], f"env:{env}"
    return None, None


def save_api_key(provider: str, key: str) -> None:
    from src.core import credentials
    key = (key or "").strip()
    if not key:
        raise ValueError("Empty API key")
    credentials.write(credential_target(provider), KEY_USER, key)


def forget_api_key(provider: str) -> None:
    from src.core import credentials
    credentials.delete(credential_target(provider))


def problems(config: dict, which: str = "main") -> list:
    """What stops this role from working, as plain sentences (empty when it is ready to try)."""
    try:
        provider, model, s = role(config, which)
    except ProviderError as e:
        return [str(e)]
    out = []
    kind = s["kind"]
    if kind == "claude_code":
        import shutil
        cli = s.get("cli_path") or "claude"
        if not shutil.which(cli):
            out.append(f"Claude Code CLI not found: {cli} — install it and log in with your Claude account")
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            out.append("claude-agent-sdk not installed — run: pip install claude-agent-sdk")
        return out
    label = s.get("label") or provider
    if not model:
        out.append(f"{label}: no model chosen — pick one in Settings › AI")
    if kind == "openai_compat" and not s.get("base_url"):
        out.append(f"{label}: no server address (base URL) set")
    if s.get("needs_key") and not api_key(provider, s)[0]:
        out.append(f"{label}: no API key saved — add it in Settings › AI")
    package = "anthropic" if kind == "anthropic" else "openai"
    try:
        __import__(package)
    except ImportError:
        out.append(f"The {package} package is not installed — run: pip install {package}")
    return out


def create_adapter(config: dict, which: str = "main", model: Optional[str] = None) -> ProviderAdapter:
    """The adapter for an API provider (not claude_code, which runs through the Claude Code backend)."""
    provider, role_model, s = role(config, which)
    model = model or role_model
    if s["kind"] == "claude_code":
        raise ProviderError("config", "claude_code runs through the Claude Code backend, not an adapter")
    if not model:
        raise ProviderError("config", f"{s.get('label') or provider}: no model chosen")
    key, _ = api_key(provider, s)
    if s.get("needs_key") and not key:
        raise ProviderError("config", f"{s.get('label') or provider}: no API key saved")
    if s["kind"] == "anthropic":
        from src.agent.providers.anthropic_api import AnthropicAdapter
        return AnthropicAdapter(provider, model, s, key)
    from src.agent.providers.openai_compat import OpenAICompatAdapter
    return OpenAICompatAdapter(provider, model, s, key)


def adapter_for(config: dict, provider: str) -> ProviderAdapter:
    """An adapter for `provider` with whatever model it has set (used to list models and test keys)."""
    s = settings_for(config, provider)
    key, _ = api_key(provider, s)
    model = str(s.get("model") or "")
    if s["kind"] == "anthropic":
        from src.agent.providers.anthropic_api import AnthropicAdapter
        return AnthropicAdapter(provider, model, s, key)
    if s["kind"] == "openai_compat":
        from src.agent.providers.openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter(provider, model, s, key)
    raise ProviderError("config", f"{provider} has no model list")
