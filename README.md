# Jarvis

**A voice assistant for Windows and macOS that actually does things on your computer, safely.**

Click the orb (or hold Space), say what you want, and Jarvis transcribes it locally, lets the AI model of
your choice decide what to do, runs the action through permission-checked tools, and answers out loud.

```
click / hold Space ─► VAD ─► Whisper (local) ─► AI model (your choice) ─► tools (policy-checked) ─► voice (local)
                                        typed text joins here ───┘
```

> **Status: alpha.** `0.1.0-alpha.1` is out for testing. Download it from
> **https://jarvis-assistant-oss.vercel.app/download**: an installer for Windows and a disk image for Macs with
> Apple silicon, each with its own Python, no administrator rights needed. A setup wizard walks you through the
> rest. Expect rough edges, and please report them.

## What works today

- **Local ears and voice**: Silero VAD and faster-whisper (GPU or CPU) for speech recognition; Kokoro voices
  by default (natural, and quick on the processor), or the system voice (Windows or macOS), Piper voices or
  Chatterbox. Without a
  microphone or voice, Jarvis still works by typing.
- **Your choice of AI** (Settings › AI): Claude through your own Claude account (the default), the
  Anthropic API, OpenAI, OpenRouter, Groq, Google Gemini, Ollama or LM Studio on your PC, or any other
  OpenAI-compatible server. API keys are stored in Windows Credential Manager or the macOS Keychain. See
  [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **Tools**: open apps, media keys, system commands, web reading and search, memory, project workspaces
  (read, edit, run tests), terminals (Command Prompt, or Terminal on a Mac), desktop control through UI
  Automation or the macOS Accessibility API, Chrome profiles, email, and
  hidden runs of a coding agent (Claude Code, Codex or Aider).
- **Agentic tasks**: multi-step goals become tracked tasks with a plan, success checks that Jarvis verifies
  itself, and pause / resume / retry. See [docs/AGENTIC_TASKS.md](docs/AGENTIC_TASKS.md).
- **One safety layer for every model**: each tool call goes through the same policy engine: schema
  validation, allow / ask / deny permissions, on-screen and spoken approvals, protected paths, timeouts and
  an audit log. Risky tool groups are off until you turn them on. High-risk approvals need a click or key.
  Actions after reading web pages or screens ask first. See
  [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md), and [SECURITY.md](SECURITY.md) to report a problem.
- **Cost visibility**: tokens and estimated cost per day in Activity, and an optional daily spending limit.
- **Desktop app**: floating orb, tray (or menu bar) icon, Ctrl+Shift+J hotkey (⌃⇧J on a Mac), no console
  windows.
- **Private**: no telemetry. Only the requests you send to your chosen AI provider leave your PC.

## Running from source

You need Windows 11 or a Mac with Apple silicon (macOS 14 Sonoma or later), Python 3.11 to 3.13, and one way to reach
an AI model (below). Details, the Mac commands, optional voices and updating are in
[docs/INSTALL.md](docs/INSTALL.md).

```
git clone <this repository> jarvis-assistant
cd jarvis-assistant
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw jarvis_gui.pyw
```

The setup wizard opens, downloads the speech model and the voice you pick, and connects the AI.

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
3. ~~Setup wizard and normal-app experience~~ (done; tests on clean machines and with new users are still to
   come)
4. ~~Safe defaults for strangers' machines: risky tool groups off until enabled, hardening~~ (done)
5. ~~Repository, documentation and CI~~ (done)
6. ~~macOS: platform layer, Mac tools, permission screens, Jarvis.app~~ (built; testing on real Macs is still to
   come)
7. Testing on clean Windows PCs and Macs, and with people who haven't seen Jarvis before
8. Website: downloads, a getting-started guide and help pages
9. First public release (an unsigned Windows installer and a disk image with the ad-hoc signed Mac app, with
   SHA-256 sums)

The work log, with what was built and how it was verified, is in [works_done.md](works_done.md), and
user-visible changes are in [CHANGELOG.md](CHANGELOG.md).

## Documentation

| | |
|---|---|
| [Getting started](docs/GETTING_STARTED.md) | Setting up and using Jarvis, for everyone |
| [Install](docs/INSTALL.md) | Running from source, optional voices, where files go |
| [AI providers](docs/PROVIDERS.md) | Claude, API keys, local models |
| [What Jarvis can do on your PC](docs/SECURITY_MODEL.md) | Tool groups, approvals, and how to limit it |
| [Tools](docs/TOOLS.md) | Every tool, its risk and when it asks |
| [Agentic tasks](docs/AGENTIC_TASKS.md) | Multi-step goals with plans and verified checks |
| [Extensions](docs/EXTENSIONS.md) | Adding your own tools |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | What the error messages mean and what to do |
| [Architecture](docs/ARCHITECTURE.md) | How the code fits together |
| [Contributing](CONTRIBUTING.md) | Development setup and rules |
| [Security policy](SECURITY.md) | Reporting a vulnerability |

## License

Jarvis is licensed under the [Apache License 2.0](LICENSE); see also [NOTICE](NOTICE). The packages it uses
keep their own licenses, listed in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). The Kokoro and Piper
voice engines include GPL-3.0 parts (for Kokoro: espeak-ng and phonemizer), so they are not part of Jarvis: the
app installs them from PyPI, on request, when you download one of their voices.
