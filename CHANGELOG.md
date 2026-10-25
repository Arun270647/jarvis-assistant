# Changelog

All notable changes to Jarvis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The detailed work log, with how each change was verified, is
[works_done.md](works_done.md).

## [Unreleased]

Nothing has been released yet. The first release will be `0.1.0-alpha.1`, with:

### Added
- Voice pipeline: local speech detection (Silero VAD) and recognition (faster-whisper), the AI, and spoken
  answers: Kokoro voices by default, or the Windows voice, Piper voices or Chatterbox. Kokoro and Piper install
  their engines on request, because those include GPL-3.0 code.
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
- Windows download: a zip with its own Python, so nothing else needs installing. Jarvis.exe starts it (and says
  so if it's still inside the zip), Install for me adds it to the Start Menu and Settings › Apps without
  administrator rights, and updates download in the background and install with Restart to update (the old
  version comes back if anything fails). Releases aren't code-signed; each one lists its SHA-256.
- macOS (Apple silicon, macOS 13 or later): Jarvis.app on a disk image (.dmg: drag it onto Applications) with
  its own Python, signed ad hoc and not notarized
  (System Settings › Privacy & Security › Open Anyway the first time). The Keychain for keys, a login item for
  start at login, ⌃⇧J as the shortcut, the menu bar icon, the Mac's own voice, Terminal windows, desktop control
  through the Accessibility API, and setup screens that show which permissions macOS has given, live. Install
  for me moves the app into Applications; updates and uninstall work as on Windows.

### Licensing
- Apache License 2.0. Nothing GPL ships with Jarvis: the Kokoro voice engine (which uses espeak-ng and
  phonemizer, GPL-3.0) and the Piper voice engine (GPL-3.0) are installed from PyPI on request, when one of
  their voices is chosen.
