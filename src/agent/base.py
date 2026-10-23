"""Shared types for agent backends."""
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.tools.registry import ToolContext


@dataclass
class AgentResult:
    reply: str
    ok: bool = True
    error: Optional[str] = None
    stage: str = "llm"
    cancelled: bool = False
    tool_calls: list = field(default_factory=list)   # [{"tool", "args", "status", "content"}]
    llm_ms: float = 0.0
    turns: int = 0
    task_id: Optional[int] = None        # set when the request became (or resumed) a tracked task
    task_status: Optional[str] = None
    spoken: Optional[str] = None         # on failure: one plain sentence for the voice (else a generic apology)


class AgentCancelled(Exception):
    pass


class AgentBackend:
    """Interface every backend implements. run() is called from the pipeline thread."""

    name = "base"
    model = ""

    def load(self) -> None:
        raise NotImplementedError

    def run(self, user_turn: str, ctx: ToolContext, emit: Callable[[dict], None]) -> AgentResult:
        raise NotImplementedError

    def cancel(self) -> None:
        """Interrupt an in-flight run (called from another thread)."""

    def reset(self) -> None:
        """Forget the conversation (new session)."""

    def unload(self) -> None:
        pass
