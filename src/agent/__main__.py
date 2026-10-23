"""Live check of the agent with the real Claude backend and the real tool registry.

    python -m src.agent check                      # dry-run tools, default prompts
    python -m src.agent check "what time is it" "remember that my favourite editor is vscode"
    python -m src.agent check --live "open notepad"

Uses a temporary memory database so the user's own database is not touched.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT_PROMPTS = [
    "what time is it right now? use your clock tool",
    "open notepad and then tell me what you did",
    "remember that my favourite colour is teal",
    "what is my favourite colour?",
]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or args[0] != "check":
        print(__doc__)
        return 2
    prompts = args[1:] or DEFAULT_PROMPTS
    live = "--live" in sys.argv

    from src.agent import create_agent
    from src.core.config_store import load_config
    from src.memory.store import MemoryStore
    from src.router.action_router import ActionRouter
    from src.tools.approvals import ApprovalBroker
    from src.tools.policy import PolicyExecutor
    from src.tools.registry import ToolContext, build_default_registry

    cfg = load_config()
    if not live:
        cfg["dry_run"] = True
    router = ActionRouter()
    router.dry_run = cfg["dry_run"]
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-check-")) / "check.db"
    memory = MemoryStore(tmp)
    events = []
    broker = ApprovalBroker(emit=events.append)
    # Console check: approvals are auto-denied so nothing sensitive runs unattended.
    broker.prompter = lambda req: broker.resolve(req.id, False, via="check script (auto-deny)")
    executor = PolicyExecutor(build_default_registry(router, memory, cfg), lambda: cfg, broker, memory, events.append)
    agent = create_agent(cfg, executor, memory)
    print(f"Backend: {agent.name} | model: {agent.model} | dry_run: {cfg['dry_run']} | tools: {executor.registry.names()}")
    t0 = time.time()
    try:
        agent.load()
    except Exception as e:
        print(f"FAIL: {e}")
        return 1
    print(f"Loaded in {time.time() - t0:.1f}s\n")
    ok = True
    try:
        for p in prompts:
            events.clear()
            t0 = time.time()
            res = agent.handle(p, "text", ToolContext(session_id="check"), events.append)
            dt = time.time() - t0
            print(f">>> {p}")
            for ev in events:
                if ev["type"] == "tool_result":
                    print(f"    tool {ev['tool']}: {ev['status']} -> {ev['content'][:160]}")
            print(f"    reply ({dt:.1f}s, ok={res.ok}): {res.reply or res.error}\n")
            ok = ok and res.ok
    finally:
        agent.unload()
    runs = memory.recent_tool_runs()
    print(f"tool_runs recorded: {len(runs)} | memories: {json.dumps([m['content'] for m in memory.list_memories()])}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
