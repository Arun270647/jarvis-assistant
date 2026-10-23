"""Jarvis agent: Claude (through the Claude Code login) driving the tool registry.

    agent = create_agent(config, executor, memory)
    agent.load()
    result = agent.handle("open notepad", "voice", ToolContext(...), emit)
"""
from typing import Callable, Optional

from src.agent.base import AgentBackend, AgentResult  # noqa: F401
from src.agent.prompt import compose_user_turn
from src.tools.registry import ToolContext


class Agent:
    def __init__(self, backend: AgentBackend, memory=None, config: Optional[dict] = None):
        self.backend = backend
        self.memory = memory
        self.config = config or {}
        self.context_providers = []      # callables -> list of extra context lines (open tasks, outcomes)

    @property
    def name(self) -> str:
        return self.backend.name

    @property
    def model(self) -> str:
        return self.backend.model

    def load(self) -> None:
        self.backend.load()

    def handle(self, text: str, source: str, ctx: ToolContext, emit: Callable[[dict], None]) -> AgentResult:
        memories = []
        n = int(self.config.get("agent", {}).get("memory_context_items", 5))
        if self.memory is not None and n > 0:
            try:
                memories = self.memory.context_memories(text, limit=n)
            except Exception as e:
                print(f"[AGENT] Memory lookup failed (continuing without): {e}")
        extra = []
        for provider in self.context_providers:
            try:
                extra += provider()
            except Exception as e:
                print(f"[AGENT] Context provider failed (continuing without): {e}")
        return self.backend.run(compose_user_turn(text, source, memories, extra), ctx, emit)

    def cancel(self) -> None:
        self.backend.cancel()

    def reset(self) -> None:
        self.backend.reset()

    def unload(self) -> None:
        self.backend.unload()


def create_agent(config: dict, executor, memory=None) -> Agent:
    """The Claude Code agent (one persistent session, tools exposed as an in-process MCP server)."""
    from src.agent.claude_code_backend import ClaudeCodeAgentBackend
    return Agent(ClaudeCodeAgentBackend(config, executor), memory=memory, config=config)
