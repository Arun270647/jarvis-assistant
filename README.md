# Jarvis

**A voice assistant for Windows that actually does things on your computer, safely.**

Click the orb (or hold Space), say what you want, and Jarvis transcribes it locally, lets the AI model of
your choice decide what to do, runs the action through permission-checked tools, and answers out loud.

```
click / hold Space ─► VAD ─► Whisper (local) ─► AI model (your choice) ─► tools (policy-checked) ─► voice (local)
                                        typed text joins here ───┘
```

> **Status: in development, not released yet.** There is no download and no setup wizard yet; you run it
> from source on Windows 11. A macOS version comes after the first Windows release.

## What works today

- **Local ears and voice**: Silero VAD and faster-whisper (GPU or CPU) for speech recognition; Piper or
  Chatterbox voices. Without a microphone or voice, Jarvis still works by typing.
- **Your choice of AI** (Settings › AI): Claude through your own Claude account (the default), the
  Anthropic API, OpenAI, OpenRouter, Groq, Google Gemini, Ollama or LM Studio on your PC, or any other
  OpenAI-compatible server. API keys are stored in Windows Credential Manager. See
  [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **Tools**: open apps, media keys, system commands, web reading and search, memory, project workspaces
  (read, edit, run tests), terminals, desktop control through UI Automation, Chrome profiles, email, and
  hidden runs of a coding agent (Claude Code, Codex or Aider).
- **Agentic tasks**: multi-step goals become tracked tasks with a plan, success checks that Jarvis verifies
  itself, and pause / resume / retry. See [docs/AGENTIC_TASKS.md](docs/AGENTIC_TASKS.md).
- **One safety layer for every model**: each tool call goes through the same policy engine: schema
  validation, allow / ask / deny permissions, on-screen and spoken approvals, protected paths, timeouts and
  an audit log.
- **Cost visibility**: tokens and estimated cost per day in Activity, and an optional daily spending limit.
- **Desktop app**: floating orb, tray icon, Ctrl+Shift+J hotkey, no console windows.
- **Phone app (optional)**: an Android companion over your private Tailscale network (`mobile/`).
- **Private**: no telemetry. Only the requests you send to your chosen AI provider leave your PC.

## Running from source

You need Windows 11, Python 3.12 or newer, and one way to reach an AI model (below).

```
git clone <this repository>
cd jarvis-assistant
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\setup_piper.py        # optional: downloads the default Piper voice
.venv\Scripts\pythonw jarvis_gui.pyw
```

Connect an AI model:

- **Claude (default)**: install [Claude Code](https://claude.com/claude-code) and sign in once with your
  Claude account. Jarvis uses that login; it never sees or stores your credentials.
- **Anything else**: open **Settings › AI**, choose the provider, save your API key (not needed for Ollama
  or LM Studio), pick a model and click **Test connection**.

The speech model (Whisper `small` by default) downloads on first use. An NVIDIA graphics card makes speech
recognition faster but isn't required.

### Where your data lives

Your settings (`config.json`, only what you changed), profile (`user_profile.json`), database and logs
live in `%APPDATA%\Jarvis`; downloaded models and caches in `%LOCALAPPDATA%\Jarvis`. Set `JARVIS_HOME` to
keep everything in one folder instead (portable or development use). The app folder itself is never
written to.

### Tests

```
.venv\Scripts\python -m unittest discover -s tests -t .
```

The tests need no microphone, GPU, network or API keys, and run in a throwaway data folder. Check a real
provider from the command line with `python -m src.agent check` (options in
[docs/PROVIDERS.md](docs/PROVIDERS.md#checking-a-provider-from-the-command-line)).

## Roadmap

1. ~~Remove personal data, per-user data folder, settings split~~ (done)
2. ~~Several AI providers~~ (done)
3. Setup wizard and normal-app experience: name your assistant, model explanations and downloads, Sign in
   with Claude, error messages with fix buttons, auto-update, uninstall
4. Safe defaults for strangers' machines: risky tool groups off until enabled, hardening
5. Repository, documentation and CI
6. Windows alpha release (signed zip)
7. macOS

The work log, with what was built and how it was verified, is in [works_done.md](works_done.md).

## License

Not chosen yet; it will be decided before the first public release. Optional components (for example the
Piper voice) have their own licenses.
