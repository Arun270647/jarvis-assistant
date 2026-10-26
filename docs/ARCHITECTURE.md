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
4. **Answering.** The reply is spoken by `src/tts` (Kokoro by default; Piper, Chatterbox or the system voice)
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

### Windows and macOS

What differs between the systems sits behind a few seams, chosen by `sys.platform`; everything else (pipeline,
agent, tasks, policy, memory, the web page) is the same code.

| Seam | Windows | macOS |
|---|---|---|
| `src/platform` (`current()`): folders, secrets, start at login, opening files and links, permissions, media keys | `windows.py`: Credential Manager, a Startup-folder shortcut, ShellExecute, virtual keys | `macos.py`: the Keychain (keyring), a LaunchAgent, `open`, AVFoundation / Accessibility / Screen Recording checks, system-defined key events |
| `src/gui/native.py`: the global shortcut, bringing a window to the front, the title bar | RegisterHotKey, AttachThreadInput, DWM | Carbon's RegisterEventHotKey (no permission needed), NSApp.activate, the dark appearance |
| The page's engine: `qt_app.make_view()`, the same QWebChannel bridge (`src/gui/bridge.py`) on both | `webengine_view.py`: Qt WebEngine (Chromium) | `webkit_view.py`: the system's WebKit (WKWebView in a Qt window container); the bridge travels through a script message handler and `evaluateJavaScript`, and an Edit menu gives text fields ⌘C and ⌘V |
| Desktop control: `src/tools/desktop_tools.py` keeps the tools and the block lists | `desktop_win.py`: UI Automation, SendInput | `desktop_mac.py`: the Accessibility API, Quartz events, CGWindowList |
| Terminals: `src/tools/terminal_tools.py` keeps the tools and the policy | `console_helper.py`: a visible cmd.exe, typed into through its input buffer | `terminal_mac.py`: Terminal.app through AppleScript; the shell's folder and programs from `ps` and `lsof`; zsh's rules for typed lines |
| Processes: `src/core/winproc.py` | no console windows, `.cmd` shims, `.lnk` shortcuts | plain subprocesses, `.app` bundles opened with `open -a`, the PATH an app from the Finder lacks |
| System voice: `src/tts/system.py` | SAPI | `say` |
| Profile defaults | `config/profile_defaults.json` | `config/profile_defaults.macos.json` |

Words the user sees follow the system too: the page's `strings.js` fills `{pc}`, `{os}`, `{keystore}` and
`{hotkey}` from the bridge, and the tool groups, wizard choices and system prompt use the platform's names.

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
- `config/profile_defaults.json` (`profile_defaults.macos.json` on a Mac) plus the user's `user_profile.json`
  describe their apps, projects and system commands.
- `config/models.json` is the model catalogue: each speech and voice model with its size, license, download
  URLs and SHA-256. It also lists add-ons, the Kokoro and Piper voice engines, which Jarvis installs on request
  but doesn't ship because they include GPL code (`src/core/addons.py`).
- `src/core/paths.py` decides where files go: settings, profile, database and logs in `%APPDATA%\Jarvis`;
  models and cache in `%LOCALAPPDATA%\Jarvis` (on a Mac: `~/Library/Application Support/Jarvis` and
  `~/Library/Caches/Jarvis`); everything in one folder when `JARVIS_HOME` is set. The app folder is never
  written to.
- `src/memory/store.py` keeps history, memories, tasks, tool runs and usage in one SQLite file.
  Schema changes are additive `MIGRATIONS`.
- API keys and passwords live in Windows Credential Manager or the macOS Keychain (`src/core/credentials.py`),
  never in files.

## The Windows installer

`packaging/build_windows.py` builds it; [CONTRIBUTING.md](../CONTRIBUTING.md#building-the-windows-installer) says
how. The installer (`packaging/windows/jarvis.iss`, Inno Setup) copies the Jarvis folder to
`%LOCALAPPDATA%\Programs\Jarvis` for the current user, adds the Start Menu shortcut and the Settings › Apps entry
(whose Uninstall runs `Jarvis.exe --uninstall`), and adds no uninstaller of its own: Jarvis removes itself.

- `Jarvis.exe` (`packaging/windows/launcher.c`) starts `runtime\pythonw.exe app\jarvis_gui.pyw`. It refuses to
  start from a temporary folder (a copy opened from inside an archive) and says so.
- The runtime is python.org's embeddable Python. Its `python313._pth` file is its whole module search path
  (the standard library, its own `site-packages` and `..\app`), so nothing installed on the PC leaks in.
- Third-party packages ship compiled only (a `.pyc` in place of each `.py`; pip and Qt's helpers keep their
  sources), without their test suites, and without PyAV: faster-whisper imports it only to read audio files, and
  its wheels bundle FFmpeg with GPL encoders. A stand-in module keeps that import working (`slim()` in the build).
- `paths.install_root()` is the Jarvis folder when the app runs from it, and `None` from source. Start-up and
  Start Menu shortcuts point at `Jarvis.exe` there (`src/core/startup.py`).
- Updates (`src/app/updates.py`): the new installer is downloaded into the cache folder and checked against its
  SHA-256. **Restart to update** writes a small hidden script and quits; the script waits for Jarvis to exit, runs
  the installer silently into the same folder (it replaces `app` and `runtime` whole), notes the result and starts
  Jarvis, which reports it.
- Install for me (`src/app/install.py`) still covers a copy of the folder run from somewhere else: it copies it to
  `%LOCALAPPDATA%\Programs\Jarvis` and registers it the way the installer does.

## The Mac app

`packaging/build_macos.py` builds it on a Mac with Apple silicon; [CONTRIBUTING.md](../CONTRIBUTING.md#building-the-mac-app)
says how.

- `Jarvis.app/Contents/MacOS/Jarvis` (`packaging/macos/launcher.c`) runs `Resources/app/jarvis_gui.pyw` on the
  bundled Python inside its own process (it links the runtime's libpython). macOS therefore treats Jarvis.app,
  with its Info.plist texts, as the app that asks for the microphone and the other permissions.
- The runtime is python-build-standalone's relocatable Python. The interpreter is isolated from the Mac's other
  Pythons and writes no `.pyc` files, because a change inside the app would break its ad-hoc signature.
- The pages render in the system's WebKit (`src/gui/webkit_view.py`), not Qt WebEngine, so the app carries no
  Chromium: Qt WebEngine and the Qt parts only it needs are about 290 MB. The pages are the same files as on
  Windows; their CSS sticks to what Safari 17 (macOS 14) supports.
- `paths.install_root()` is the `Jarvis.app` folder. Start at login is a LaunchAgent that runs `open -a` on it.
- The download is a disk image (`.dmg`) with Jarvis.app and a link to Applications. Updates attach the new one
  read-only and out of sight, copy the app out with `ditto` into the cache folder and detach it; **Restart to update** runs a small shell script
  (`MAC_UPDATER` in `src/app/updates.py`) that swaps the apps after Jarvis quits, keeps the old one until the
  next start, and puts it back if a move fails. A Jarvis.app that macOS runs from a read-only place (App
  Translocation, straight from Downloads) offers the download instead.
- Install for me copies Jarvis.app into `/Applications` (or `~/Applications`) with `ditto`, then opens it from
  there and deletes the download; started from the disk image, it ejects the image and deletes the `.dmg`.

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
