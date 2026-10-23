# Jarvis tool extensions

When Jarvis reports a missing capability (`note_capability_gap` in a task timeline), it can be added here as a tool.

1. Create `tools_ext/<name>.py` with a `register(registry, config)` function:

   ```python
   from src.tools.registry import ToolResult, ToolSpec

   def register(registry, config):
       def handler(args, ctx):
           # ... do the work; return the real result ...
           return ToolResult(ok=True, content="...")

       registry.register(ToolSpec(
           name="my_tool",
           description="What it does and when Claude should use it.",
           input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
           handler=handler,
           risk="sensitive",          # safe | sensitive | destructive
           permission="ask",          # extension tools that are not "safe" are forced to ask
           idempotent=False,          # duplicate protection inside a task
           requires_task=True,        # only inside a planned, verified task
       ))
   ```

2. Enable it in your settings file (`config.json` in Jarvis's data folder, `%APPDATA%\Jarvis` by default):
   `"tools": {"extensions": ["<name>"]}`, then restart the assistant.

Rules:
- The agent cannot write here or enable extensions: `tools_ext/` and `config/` are protected paths.
- Extension tools still go through `PolicyExecutor`: schema validation, permissions and approvals, budgets, audit.
- Use `src.core.winproc` for any subprocess, so no console window ever appears.
