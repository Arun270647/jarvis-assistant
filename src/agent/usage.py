"""Token usage and estimated cost of model requests, and the optional daily budget.

Prices come from llm.prices in the settings (USD per million tokens, per model id; users can add their
own). Models on this PC (providers marked local) and the Claude subscription (claude_code) cost nothing
extra, so they are recorded with cost 0; a model without a price is recorded with no cost.

With llm.daily_budget_usd set, the agent refuses new requests once today's estimated cost reaches it.
"""
import time
from typing import Callable, Optional

from src.agent.providers.base import Usage


def price_for(config: dict, provider_settings: dict, model: str) -> Optional[dict]:
    """{"input", "output", "cache_read", "cache_write"} in USD per million tokens, or None when unknown."""
    if provider_settings.get("local") or provider_settings.get("kind") == "claude_code":
        return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
    prices = (config.get("llm", {}) or {}).get("prices", {}) or {}
    p = prices.get(model)
    if not isinstance(p, dict) or "input" not in p or "output" not in p:
        return None
    inp = float(p["input"])
    return {"input": inp, "output": float(p["output"]),
            "cache_read": float(p.get("cache_read", inp * 0.1)), "cache_write": float(p.get("cache_write", inp * 1.25))}


def cost_usd(usage: Usage, price: Optional[dict]) -> Optional[float]:
    if price is None:
        return None
    return (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]
            + usage.cache_read * price["cache_read"] + usage.cache_write * price["cache_write"]) / 1_000_000


def today() -> str:
    return time.strftime("%Y-%m-%d")


class UsageMeter:
    """Records usage in the memory store and answers budget questions. Works without a store (no totals)."""

    def __init__(self, memory, config_getter: Callable[[], dict]):
        self.memory = memory
        self._config = config_getter
        self._spent_without_store = 0.0

    def record(self, provider: str, model: str, usage: Usage, provider_settings: dict, role: str = "main") -> dict:
        """Store one request's usage; returns the `usage` event for the UI."""
        cost = cost_usd(usage, price_for(self._config(), provider_settings, model))
        if self.memory is not None:
            try:
                self.memory.add_llm_usage(provider, model, usage.input_tokens, usage.output_tokens,
                                          usage.cache_read, usage.cache_write, cost, role=role)
            except Exception as e:
                print(f"[USAGE] Could not record usage: {e}")
        else:
            self._spent_without_store += cost or 0.0
        return {"type": "usage", "role": role, "provider": provider, "model": model,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "cache_read": usage.cache_read, "cache_write": usage.cache_write,
                "cost_usd": None if cost is None else round(cost, 6), "today_usd": round(self.spent_today(), 4)}

    def spent_today(self) -> float:
        if self.memory is None:
            return self._spent_without_store
        try:
            return self.memory.usage_cost_on(today())
        except Exception:
            return 0.0

    def budget(self) -> Optional[float]:
        value = (self._config().get("llm", {}) or {}).get("daily_budget_usd")
        try:
            value = float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
        return value if value and value > 0 else None

    def over_budget(self) -> Optional[float]:
        """The daily budget when today's spending has reached it, else None."""
        limit = self.budget()
        if limit is not None and self.spent_today() >= limit:
            return limit
        return None


def budget_message(limit: float) -> str:
    return (f"I've reached today's AI spending limit of {limit:.2f} dollars, so I'm pausing until tomorrow. "
            "You can raise the limit in Settings.")
