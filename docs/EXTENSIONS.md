# Extensions

An extension adds a tool that Jarvis doesn't have, as a Python file in the `tools_ext` folder of the app.

> **Extensions run with your full Windows user rights, and nobody has reviewed them.** An extension can do
> anything a program you run can do. Turn one on only if you wrote it or trust whoever did.

## Turning one on

Put the file in `tools_ext`, then tick it in **Settings › Extensions** and restart the assistant. Jarvis
loads only the extensions ticked there (the `tools.extensions` setting), never every file in the folder.

The assistant itself can't add or turn on extensions: `tools_ext` and the settings are protected from its file
tools.

## Writing one

A module with a `register(registry, config)` function that registers one or more tools:

```python
from src.tools.registry import ToolResult, ToolSpec


def register(registry, config):
    def handler(args, ctx):
        # Do the work, then return what really happened. Never invent a result.
        return ToolResult(ok=True, content="...")

    registry.register(ToolSpec(
        name="my_tool",
        description="What it does and when the AI should use it.",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        handler=handler,
        risk="sensitive",          # safe | sensitive | destructive
        permission="ask",          # an extension tool that isn't "safe" is made to ask anyway
        idempotent=False,          # True if running it twice has no second effect
        requires_task=True,        # only inside a planned, verified task
    ))
```

What Jarvis does with it:

- The tool goes through `PolicyExecutor` like a built-in one: argument validation, permissions, approvals,
  call limits, time limits, masking of secrets and the audit log.
- An extension tool that isn't `safe` asks by default. You can change that in **Settings › Tools and
  permissions**, where it shows up in the "Extension" group.
- Extension tools belong to no tool group, so the group switches don't hide them. Untick the extension
  instead.
- If the tool returns text from files, web pages or programs, set `untrusted=True` on the `ToolResult`. The
  AI then treats the text as data, and later risky calls in the same request ask first.

Rules for extension code:

- Start subprocesses through `src.core.winproc` (`run_hidden`, `popen_hidden`), so no console window appears.
- Keep secrets out of the result. `src.core.redact.redact()` masks common key formats.
- Raise or return `ok=False` with a plain-language message when something fails. The AI reads it and can
  explain the failure or try something else.

The `note_capability_gap` tool records what the assistant couldn't do during a task. Those notes, in the
Agent view, are good ideas for an extension.
