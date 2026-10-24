# Changelog

All notable changes to Jarvis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The detailed work log, with how each change was verified, is
[works_done.md](works_done.md).

## [Unreleased]

Nothing has been released yet. The first release will be `0.1.0-alpha.1`, with:

### Added
- Voice pipeline: local speech detection (Silero VAD) and recognition (faster-whisper), the AI, and spoken
  answers (the Windows voice, Piper voices as an add-on, or Chatterbox).
- Sign in with Claude (the default, through Claude Code) and other ways to connect: an Anthropic API key, or
  any OpenAI-compatible provider (OpenAI, OpenRouter, Groq, Google Gemini, Ollama, LM Studio).
- Tools for apps, media, system commands, the web, memory, project folders, Command Prompts, desktop control,
  Chrome profiles, email and coding agents, all through one permission-checked policy engine with approvals
  and an audit log.
- Safe defaults: risky tool groups off until you turn them on, a rule that asks before changes after reading
  web pages or screens, and approvals that need a click or key for high-risk actions.
- Agentic tasks with plans, independently verified success checks, and pause, resume and retry.
- Desktop app with a floating orb, tray icon, global hotkey, setup wizard, error messages with fix buttons,
  update check and uninstall.
- Optional Android phone app over Tailscale.

### Licensing
- Apache License 2.0. The Piper voice engine (GPL-3.0) is no longer a requirement: the app installs it on
  request when a Piper voice is chosen.
