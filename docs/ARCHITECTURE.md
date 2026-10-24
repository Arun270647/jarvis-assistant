# Architecture

How Jarvis is put together, for people who want to change it. For what it does and how to use it, start with
the [README](../README.md) and [GETTING_STARTED.md](GETTING_STARTED.md).

## One request, start to finish

```
microphone ─► speech detection ─► speech recognition ─┐
typed text ───────────────────────────────────────────┤
                                                      ▼
                          pipeline (src/pipeline.py, one thread)
                                                      │
                          agent: orchestrator + backend (src/agent)
                                                      │ tool calls
                          PolicyExecutor (src/tools/policy.py) ── approvals (dialog, voice)
                                                      │
                          tools (src/tools/*) ── router, workspace, memory, desktop, email …
                                                      │ real results
                          agent's answer ─► voice (src/tts) + window
```

1. **Hearing.** `src/audio/capture.py` reads the microphone. `src/stt/vad.py` (Silero through ONNX Runtime)
   finds speech, and `src/stt/transcriber.py` (faster-whisper) turns it into text. Typed commands join here.
2. **Thinking.** `src/agent/orchestrator.py` runs the conversation turn with the configured AI
   (`llm.main`). Two backends exist:
   - `claude_code_backend.py` drives Claude through the Claude Code CLI ("Sign in with Claude");
   - `tool_loop_backend.py` is a generic tool-calling loop for API providers, through the adapters in
     `src/agent/providers/` (Anthropic API, and anything OpenAI-compatible).
   `src/agent/prompt.py` builds the system prompt, and `src/agent/toolsets.py` decides which tools a small model
   is shown.
3. **Acting.** Every tool call goes through `PolicyExecutor.execute()`. It validates the arguments, applies
   permissions and the safety rules, asks for approval through `ApprovalBroker`, runs the handler with a time
   limit, masks secrets, and records the call. Nothing else in the code performs actions on the PC.
4. **Answering.** The reply is spoken by `src/tts` (Kokoro by default; Piper, Chatterbox or the Windows voice)
   and shown in the window.

Multi-step goals become **tasks** (`src/agent/tasks.py`). A task has a plan, success checks that are verified
independently, and can be paused, resumed or retried. See [AGENTIC_TASKS.md](AGENTIC_TASKS.md).

## Layers

```
src/gui                  the desktop window (PySide6 + a web page) and the floating orb
src/app                  AssistantController (the one object front-ends talk to), setup wizard, errors, updates
src/pipeline.py          the voice loop: hearing, the agent, speaking, approvals by voice
src/agent                the AI: providers, backends, orchestrator, tasks, usage and cost
src/tools                the tool registry, PolicyExecutor, approvals, tool groups, every tool
src/router, src/memory   app/media/system actions behind some tools; the SQLite store
src/core, src/platform   settings, paths, credentials, downloads, models, redaction, subprocesses, OS services
```

Code imports downward only. The one exception is `src/core/diagnose.py`, which reports on everything,
including which AI provider is configured.

- **The window never reaches past the controller.** The desktop window (`src/gui/bridge.py`) calls
  `AssistantController` (`src/app/controller.py`) and listens to its events.
- **The pipeline runs in its own thread.** The controller starts it and passes commands in through a queue.
  Events flow back as plain dicts.
- **Qt widgets are only touched on the UI thread.** Worker threads emit events, and `qt_app.py` applies them
  in a timer on the UI thread.

## Settings and data

- `config/defaults.json` holds every setting with its default. The user's `config.json` stores only what they
  changed, and `JARVIS__section__key` environment variables override both (`src/core/config_store.py`).
  Settings are validated against `config_store.EDITABLE`. Older settings files are upgraded by `MIGRATIONS`.
- `config/profile_defaults.json` plus the user's `user_profile.json` describe their apps, projects and system
  commands.
- `config/models.json` is the model catalogue: each speech and voice model with its size, license, download
  URLs and SHA-256. It also lists add-ons, the Kokoro and Piper voice engines, which Jarvis installs on request
  but doesn't ship because they include GPL code (`src/core/addons.py`).
- `src/core/paths.py` decides where files go: settings, profile, database and logs in `%APPDATA%\Jarvis`;
  models and cache in `%LOCALAPPDATA%\Jarvis`; everything in one folder when `JARVIS_HOME` is set. The app
  folder is never written to.
- `src/memory/store.py` keeps history, memories, tasks, tool runs and usage in one SQLite file.
  Schema changes are additive `MIGRATIONS`.
- API keys and passwords live in Windows Credential Manager (`src/core/credentials.py`), never in files.

## The Windows zip

`packaging/build_windows.py` builds it; [CONTRIBUTING.md](../CONTRIBUTING.md#building-the-windows-zip) says how.

- `Jarvis.exe` (`packaging/windows/launcher.c`) starts `runtime\pythonw.exe app\jarvis_gui.pyw`. It refuses to
  start from inside the zip or a temporary folder and says how to extract the zip.
- The runtime is python.org's embeddable Python. Its `python313._pth` file is its whole module search path
  (the standard library, its own `site-packages` and `..\app`), so nothing installed on the PC leaks in.
- `paths.install_root()` is the Jarvis folder when the app runs from the zip, and `None` from source. Start-up
  and Start Menu shortcuts point at `Jarvis.exe` there (`src/core/startup.py`).
- Updates (`src/app/updates.py`): the new zip is downloaded, checked against its SHA-256 and unpacked into
  `Jarvis\.update`. **Restart to update** starts a copy of `Jarvis-Updater.exe` (`packaging/windows/updater.c`)
  from the temporary folder and quits. The updater waits for Jarvis to exit and moves each item into place,
  keeping the old one in `Jarvis\.old`. If a move fails, it puts everything back. Then it starts Jarvis, which
  reports the result and deletes `.old`.
- Install for me (`src/app/install.py`) copies the folder to `%LOCALAPPDATA%\Programs\Jarvis`, adds the Start
  Menu shortcut and the Settings › Apps entry (whose Uninstall runs `Jarvis.exe --uninstall`), and starts the
  installed copy after this one quits.

## Rules the code follows

- **Every action is a tool executed through `PolicyExecutor`.** New abilities are new tools with a closed JSON
  schema, a risk level, a default permission, a category and a group (`src/tools/groups.py`). A test fails if a
  tool has no group.
- **Every subprocess goes through `src/core/winproc.py`** (or passes `creationflags` explicitly), so no
  console window ever flashes. Never `shell=True`. A test scans the code for this.
- **Untrusted output is marked.** Tools that return text from files, web pages, programs or screens set
  `ToolResult.untrusted`. The AI receives it wrapped as data, and the untrusted-content rule makes later risky
  calls ask.
- **Secrets are masked** (`src/core/redact.py`) before anything is logged, stored, shown or sent to the AI.
- **The agent can't weaken its own rules.** The app folder, `config/`, `tools_ext/` and the data folders are
  protected paths for the file tools.

## Tests

`python -m unittest discover -s tests -t .` runs everything without a microphone, GPU, network or API keys.
`tests/__init__.py` points the run at a throwaway data folder. `tests/fakes.py` has a fake microphone,
transcriber, voice and AI backend, so the real pipeline runs end to end. Files named `tests/live_*.py` are manual
checks against real hardware and providers and don't run in CI.
