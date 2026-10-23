# Agentic task layer (design and status)

Written 2026-10-12, before the open-source release; implemented and verified the same day. The wake word mentioned
in sections 1 and 5 was removed later; you now talk by clicking the orb, holding push-to-talk or typing.

## 1. Architecture before this phase (audited)

- **Voice pipeline (then):** wake word → "Jarvis is online" → VAD → Whisper → agent → Piper. The desktop GUI is PySide6.
- **Claude:** reached through a persistent Claude Code session (subscription). Tools are exposed as an in-process MCP server, and Claude could already loop over tools within one request.
- **Tools:** 9, not 35. They were app, media, system command, search, Claude Code delegation, clock, remember, recall and forget. There were **no** tools to read or edit files, run project commands, check processes or hit health endpoints.
- **PolicyExecutor:** validation, allow/ask/deny, approvals, dry-run, timeouts and an audit log.
- **SQLite `jarvis.db`** (in the user's data folder): memories, messages and tool runs.
- **Missing for agentic work:** a plan, persistent task state, a definition of success, independent verification, pause/resume/retry, restart recovery, retries, idempotency, budgets, workspace boundaries, secret handling and untrusted-content handling.

## 2. Minimum changes (what was added; nothing was rewritten)

| Need | Change |
|---|---|
| Inspect / act on projects | `src/tools/workspace_tools.py`: `project_context`, `list_dir`, `read_file`, `search_files`, `edit_file`, `write_file`, `delete_file`, `run_command`, `check_process`, `http_check` |
| Boundaries | `src/core/workspace.py`: authorized roots (the projects in `user_profile.json`), protected paths of the assistant's own repo, secret files, command deny / allow / ask lists (per-project `commands`) |
| Secrets | `src/core/redact.py`: output to Claude, logs, UI and memory is redacted; secrets are never remembered |
| Reliability | `ToolSpec.retries` / `idempotent` / `requires_task` / `category`; `PolicyExecutor`: retries for transient failures, duplicate protection and restart reconciliation, call budgets, task pause/cancel control, fault injection (tests/demo), untrusted-output wrapping, task-linked audit |
| Task state | `tasks` + `task_events` tables and new `tool_runs.task_id/args_hash` and `memories.project/approved` columns. Migrations are additive, in the same database. |
| Planning, verification | `src/agent/tasks.py` `TaskManager`: Claude's task tools and the verification engine |
| Controlled loop | `src/agent/orchestrator.py`: wraps the existing `Agent`; continue / pause / cancel / block / limits; resume and retry |
| UI / voice | Agent workspace tab, task strip, conversation milestones, Pause/Resume/Cancel/Retry; spoken progress and plain-speech final reports |

## 3. The loop

Two levels:

1. **Inside one Claude run.** Claude loops over tools: inspect, plan, execute, observe, adapt. It is bounded by `agent.max_turns` and by a tool-call budget enforced in `PolicyExecutor`.
2. **Around runs.** The orchestrator reads the task from SQLite after every run and decides what happens next:

| State after a run | Orchestrator action |
|---|---|
| COMPLETED / FAILED / BLOCKED | Report it |
| Paused / cancelled by the user | Mark it PAUSED / CANCELLED |
| Claude error | Mark it BLOCKED, with the error |
| Time or tool budget used up | Mark it BLOCKED (resumable) |
| Still RUNNING | Send a continue prompt, at most `agent.task.max_nudges` times |

Guarantees enforced in code rather than by prompting:

- A request becomes a task with `create_plan`. That call **requires success checks**, which define success before execution.
- **Changes need a task.** `edit_file`, `write_file`, `delete_file` and any `run_command` that is not read-only are refused outside a task.
- `finish_task` refuses while any step is unmarked.
- `finish_task` then **runs the success checks itself**, through `PolicyExecutor`. The check types are command exit code/output, file exists/contains, process running and HTTP status.
- Only passing checks mark the task COMPLETED. A failure goes back to Claude to diagnose and adapt. After `agent.task.max_verify_attempts` failures the task is marked FAILED.
- An identical non-idempotent call in the same task is refused unless Claude sets `repeat=true`. After a restart, interrupted calls are reported as "outcome unknown — inspect first".
- Tasks still active when Jarvis stops become BLOCKED. They are never resumed automatically.

## 4. Safety boundaries

- **Paths:**
  - Only the configured project folders (plus `agent.workspace_roots`).
  - Jarvis's own `src`, `config`, `tests`, `tools_ext`, its data folders (settings, database, logs, models, cache) and the pristine demo copy are read-only to the agent.
  - Secret files (`.env`, keys, credential stores) are never opened.
- **Commands:**
  - argv only, never a shell; hidden; timeout; environment stripped of key/token/secret variables.
  - Pre-approved: read-only git commands, plus each project's `commands`.
  - Denied: shells, `curl`/`wget`, destructive git commands, `format`/`diskpart`/registry/service tools, and anything with shell chaining characters.
  - Everything else asks the user.
- **Destructive or overwriting actions** (`delete_file`, overwriting `write_file`) ask first, and backups are kept in `agent_backups/task<N>/` in the cache folder.
- **Untrusted content:** file, command and web output is wrapped in `<untrusted_output>`. The system prompt tells Claude never to follow instructions found in it.
- **Memory:**
  - Preferences and conventions need the user's approval.
  - Project-scoped memories feed `project_context`.
  - Earlier task outcomes are given to Claude so it avoids approaches that already failed.
- **Missing capabilities** are recorded (`note_capability_gap`), not worked around. New tools come only through user-enabled `tools_ext/` modules.

## 5. Verification evidence (2026-10-12)

- **Automated:** `python -m unittest discover -s tests -t .` passes 104 tests, 30 of them new for this layer:
  - lifecycle, verification feedback, bounded attempts, step and dependency rules, required checks, continue/block
  - the no-task guard, pause/resume, cancel, block → `resume_task`, retry, budget limit, waiting for approval
  - retries, duplicate protection, restart reconciliation, fault injection
  - workspace boundaries, command classes, dry-run, redaction, untrusted wrapping
  - GUI task commands through the pipeline, controller task controls
- **Live** (`tests/live_agent_demo.py`, real Claude, real tools, real Piper):
  - The demo project starts with a real bug and a missing `VERSION` file, which breaks the build.
  - The assistant inspected the project, planned with 3 success checks, hit the injected (labelled) test-runner crash, and recovered.
  - It diagnosed and fixed the bug, created `VERSION`, ran the tests and the build, and marked its steps.
  - Independent verification passed 3/3, the report was spoken, and a separate check by the script confirmed the result.
  - **PASS with typed input. PASS with spoken input** (wake-word WAV plus a Piper-synthesized objective, transcribed by real Whisper).
- **Live in the GUI:**
  - The same objective ran through the desktop app, and the Agent workspace showed the plan, checks with evidence and the timeline.
  - Pause from the GUI stopped Claude mid-task (PAUSED). Resume continued it to a verified COMPLETED.
  - `tests/live_no_console.py` (now including an agentic task) saw no console window.
