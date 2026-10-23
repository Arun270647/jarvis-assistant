"""Live check of the agent with a real AI provider and the real tool registry.

    python -m src.agent check                      # the configured provider (llm.main), dry-run tools
    python -m src.agent check "what time is it" "remember that my favourite editor is vscode"
    python -m src.agent check --live "open notepad"
    python -m src.agent check --provider openai --model gpt-x
    python -m src.agent check --provider ollama --model qwen3:8b
    python -m src.agent check --provider custom --base-url http://127.0.0.1:8000/v1 --model my-model

--provider picks a key of llm.providers for this run only; --model and --base-url override that provider's
settings. The API key comes from Settings (Windows Credential Manager) or the provider's environment
variable (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...). Uses a temporary memory database so the user's own
database is not touched.
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
VALUE_FLAGS = ("--provider", "--model", "--base-url")


def parse(argv: list) -> tuple:
    """(positional args, {flag: value}, set of bare flags). Accepts --flag value and --flag=value."""
    args, values, flags = [], {}, set()
    i = 0
    while i < len(argv):
        a = argv[i]
        name, _, inline = a.partition("=")
        if name in VALUE_FLAGS:
            if inline:
                values[name] = inline
            elif i + 1 < len(argv):
                values[name] = argv[i + 1]
                i += 1
        elif a.startswith("--"):
            flags.add(a)
        else:
            args.append(a)
        i += 1
    return args, values, flags


def main() -> int:
    args, values, flags = parse(sys.argv[1:])
    if not args or args[0] != "check":
        print(__doc__)
        return 2
    prompts = args[1:] or DEFAULT_PROMPTS
    live = "--live" in flags

    from src.agent import create_agent
    from src.agent.providers import problems
    from src.core.config_store import load_config
    from src.memory.store import MemoryStore
    from src.router.action_router import ActionRouter
    from src.tools.approvals import ApprovalBroker
    from src.tools.policy import PolicyExecutor
    from src.tools.registry import ToolContext, build_default_registry

    cfg = load_config()
    if not live:
        cfg["dry_run"] = True
    provider = values.get("--provider")
    if provider:
        if provider not in cfg["llm"]["providers"]:
            print(f"Unknown provider {provider!r}; known: {', '.join(cfg['llm']['providers'])}")
            return 2
        cfg["llm"]["main"]["provider"] = provider
    main_provider = cfg["llm"]["main"]["provider"]
    if "--model" in values:
        cfg["llm"]["providers"][main_provider]["model"] = values["--model"]
    if "--base-url" in values:
        cfg["llm"]["providers"][main_provider]["base_url"] = values["--base-url"]
    issues = problems(cfg, "main")
    if issues:
        print("FAIL: " + "; ".join(issues))
        return 1
    router = ActionRouter()
    router.dry_run = cfg["dry_run"]
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-check-")) / "check.db"
    memory = MemoryStore(tmp)
    events = []
    broker = ApprovalBroker(emit=events.append)
    # Console check: approvals are auto-denied so nothing sensitive runs unattended.
    broker.prompter = lambda req: broker.resolve(req.id, False, via="check script (auto-deny)")
    executor = PolicyExecutor(build_default_registry(router, memory, cfg), lambda: cfg, broker, memory, events.append)
    from src.agent.tasks import TaskManager
    TaskManager(memory, executor, lambda: cfg, events.append).register_tools(executor.registry)
    agent = create_agent(cfg, executor, memory)
    offered = getattr(agent.backend, "tool_names", executor.registry.names())
    print(f"Backend: {agent.name} | model: {agent.model} | dry_run: {cfg['dry_run']} | {len(offered)} tools: {offered}")
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
                elif ev["type"] == "usage":
                    cost = "" if ev.get("cost_usd") is None else f", ${ev['cost_usd']:.4f}"
                    print(f"    usage: {ev['input_tokens']} in, {ev['cache_read']} cached, {ev['cache_write']} "
                          f"cache write, {ev['output_tokens']} out{cost}")
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
