# Work log

What was built, how it was verified, and what was decided along the way. Newest first.
The phase plan and the release checklist live in the maintainer's planning notes (not in the repo);
each entry names the phase it belongs to.

---

## 2026-10-23 · Phase 2: several AI providers

### What changed

**Providers** (`src/agent/providers/`)
- Neutral message types and the `ProviderAdapter` interface (`base.py`): text, tool calls, tool results
  with images, usage (input, output, cache read, cache write), capability flags, and one `ProviderError`
  with a `kind` (auth, rate_limit, overloaded, timeout, network, context_length, ...) and a spoken sentence.
  Every request runs in a worker thread, so cancel returns at once and an open stream is closed.
- `openai_compat.py`: chat completions with tools through the official `openai` SDK. One adapter for
  OpenAI, OpenRouter, Groq, Gemini (OpenAI-compatible endpoint), Ollama, LM Studio and any compatible
  server. Malformed tool arguments are flagged and sent back exactly as the model wrote them; images from a
  tool follow the tool messages as a user message; a "basic" schema mode drops validation keywords for
  servers that reject them; vision is set per provider or guessed from the model name.
- `anthropic_api.py`: the Messages API through the official `anthropic` SDK, always streamed (the timeout
  is a limit on silence). Prompt caching: a breakpoint on the system prompt (tools + system cached
  together) plus automatic caching of the conversation. Assistant content, including signed thinking
  blocks, goes back unchanged. Effort is sent and dropped for models that reject it. For Claude Fable 5.1,
  Opus 5.5, Opus 5 and Sonnet 5.5 on the official API, a refused request is re-run on Anthropic's
  recommended fallback model (`fallbacks: "default"`); blocks from a declined partial answer are neither run
  nor sent back.
- Settings › AI picks the provider; API keys live in Windows Credential Manager (`JARVIS/LLM/<provider>`),
  with the provider's environment variable as a fallback for developers.

**The agent for API providers** (`src/agent/tool_loop_backend.py`)
- A generic tool-calling loop behind the existing `AgentBackend` interface. Every call goes through
  `PolicyExecutor` (validation, permissions, approvals, dry run, audit log), one at a time. Unknown tools,
  invalid or malformed arguments and denied calls come back as results the model can read; every call gets
  a result, also when the user stops halfway or a tool raises.
- Cancel and the task pause interrupt stop the request in flight; `agent.max_seconds` bounds the request;
  max turns ends with the last thing the model said; screenshots go to models that can see and become a
  text note otherwise (a server that refuses images is retried without them).
- The conversation only grows (needed for signed thinking blocks, and it keeps prompt caching effective).
  When the last request read more than `history_budget_tokens`, the next turn starts from a summary written
  by the utility model, or a short note if that fails. A start-up request checks the key and model, loads a
  local model and fills the prompt cache.
- `create_agent()` reads `llm.main`: Claude Code for `claude_code`, the tool loop for everything else.
- The Claude Code backend reads the new settings, honours the tool profile and records its token usage
  (part of the Claude plan, so no cost).

**Roles, tools, usage**
- Utility role (`src/agent/utility.py`): greetings and conversation summaries run on `llm.utility`, by
  default the main provider's small model (Haiku via `claude -p` for Claude). Greetings no longer call
  `claude -p` themselves.
- Tool profiles (`src/agent/toolsets.py`, `tools.profile`): `full`, `core` (apps, media, clock, memory, web
  reading, read-only project files, tasks), `custom`, and `auto` (core for providers on this PC). A backend
  refuses calls to tools it did not offer.
- System prompt: sections about tools the model doesn't have are left out, so a model with the short list
  isn't told about desktop control or terminals; the terminal section names the configured coding agent.
- Usage and cost (`src/agent/usage.py`, table `llm_usage`): every request's tokens and estimated cost
  (prices per model in `llm.prices`, Claude models listed; local models and the Claude plan cost 0) are
  stored and sent as a `usage` event. Activity shows an "AI usage" card with daily totals. With
  `llm.daily_budget_usd` set, new requests stop once today's estimate reaches it, with a spoken explanation.

**Coding agent delegation**
- `delegate_to_claude` became `delegate_to_coding_agent`, configured by `coding_agent.cli`: Claude Code
  (`claude -p`), Codex (`codex exec`), Aider (`aider --yes-always --message`), a custom command, or off (the
  tool is not offered). The approval dialog names the agent; output still goes to the cache folder
  (`coding_agent_outputs/`), never into the project. The output pipes are now closed after each run.
- The spoken yes/no words for approvals moved from `claude_code` to `voice_approval`.

**Settings**
- `config/defaults.json` (config version 2): `llm.main`, `llm.utility`, shared request settings and nine
  provider presets (Claude through Claude Code as the default, Anthropic API, OpenAI, OpenRouter, Groq,
  Gemini, Ollama, LM Studio, another OpenAI-compatible server), `llm.prices`, `coding_agent`,
  `voice_approval`, `tools.profile`.
- `migrate_user_config()` moves a version 1 file: `llm.claude` to `llm.providers.claude_code`,
  `greeting.model` to its utility model, `claude_code` to `coding_agent` (off or a custom command when it
  was disabled or pointed elsewhere) and `voice_approval`, and the tool permission to the new tool name.
- New editable settings for the providers, roles, budget, tool profile and coding agent, with validation
  (known provider names, nullable budget and vision, effort values).

**UI**
- Settings › AI replaces the Claude section: provider, model with Fetch models, effort, server address,
  timeout, vision, API key (write-only: Save key / Forget key and where the key comes from), badges (cloud
  or on this PC, tools, images, prompt caching), an estimated cost per request from the prompt size and the
  price table, the background model, tool profile, limits, daily budget and Test connection (any provider:
  the model has to use the clock tool).
- Tools and permissions: the coding agent choice. Activity: the AI usage card.
- Spoken errors use the provider's sentence ("The AI service rejected the API key ..."); the ready event
  names the actual backend.

**Live check**
- `python -m src.agent check --provider <name> --model <id> [--base-url <url>]` runs against any
  provider, with the planning tools registered like in the app.

**Docs**
- `docs/PROVIDERS.md`: choosing and setting up providers, local models (context length, short tool list),
  the two roles, cost and the daily limit, coding agents, the live check, and how the code is organised.

**Dependencies**
- `anthropic` and `openai` (both Apache-2.0, imported only when that provider is used).

### Verified
- 300 automated tests pass (`python -m unittest discover -s tests -t .`), 92 of them new:
  - `tests/test_providers.py`: both adapters against a fake HTTP server (`tests/fake_llm.py`): plain text,
    single and parallel tool calls, a failed tool result, image results, cancel mid-request, timeout,
    malformed tool JSON, 429 with retry-after, persistent rate limit, rejected key, unreachable server,
    model list, prompt-cache breakpoints, thinking blocks sent back unchanged, effort retried without,
    refusal fallback rules; plus provider roles, key lookup and readiness checks.
  - `tests/test_tool_loop.py`: the loop with a scripted model and the real `PolicyExecutor`.
  - `tests/test_backend_contract.py`: the same five conversations on four backends (scripted, OpenAI over
    HTTP, Anthropic over HTTP, Claude Code with a fake SDK client).
  - `tests/test_llm_setup.py` and `tests/test_config_store.py`: backend choice, utility role, tool
    profiles, prices and the daily budget, coding agent presets and a real hidden run, the version 1 to 2
    migration and the new settings' validation.
- Phone app: `npx tsc --noEmit` clean (unchanged).
- End to end: `python -m src.agent check` against fake OpenAI-compatible and Anthropic servers (real
  registry with 45 tools, planning tools, memory, dry run): both PASS, with tool calls, a saved memory,
  cached-token usage and cost. One real request through the default Claude Code path: PASS (clock tool used,
  usage recorded).
- GUI smoke run with a scratch data folder: Settings › AI renders, switching the provider shows that
  provider's fields, Fetch models reports an unreachable server in plain words, and the Activity usage card
  renders; no page errors after the fix below.
- Personal-data scan over every changed or new file: 0 hits. `ruff check --select F` on the changed modules:
  clean apart from one f-string in `src/pipeline.py` that predates this work.

### Fixed along the way
- Activity's loader could run unbound from a timer and fail to load the usage card.
- The coding-agent run left its output pipes open (a ResourceWarning in tests).

### Decided
- The live check against real providers (an Anthropic API key, an OpenAI-compatible cloud service, a local
  Ollama model) moves to the release checklist: which provider to use is each user's choice, so it is
  verified once before the first release. Until then the adapters are tested against fake servers.

### Not done yet
- Signed-out and usage-limit banners, the Sign in with Claude flow and per-provider setup screens are part
  of the setup wizard (Phase 3).

---

## 2026-10-23 · Phase 5 start: first commit, private GitHub repository

- First commit of the Phase 1 state, pushed to a private GitHub repository; it becomes public at
  release (Phase 6). The CI workflow, LICENSE and the public docs follow later in Phase 5.
- Before committing: the personal-data scan over exactly the files being committed found 0 hits,
  no private or user files were staged, and the 208 tests passed.
- `.gitignore`: removed the template's "copy this file" note from the first line.

---

## 2026-10-23 · Phase 1 complete: de-personalized, per-user data folder, config split

### What changed

**Where files live** (`src/core/paths.py`)
- The app folder holds code and the shipped `config/` only; nothing is written there at run time.
- The user's data folder (`%APPDATA%\Jarvis`) holds `config.json`, `user_profile.json`, `jarvis.db`
  and `logs/`. Large local files go to `%LOCALAPPDATA%\Jarvis\models` and `\cache`.
- `JARVIS_HOME` overrides all of it (portable/dev mode: one folder holds everything; a relative path
  is relative to the app folder).
- Replaced every repo-relative `data/`, `.cache/` and `config/config.json` path: memory store,
  logging, command log, STT corrections log, warm-up clip, TTS caches, agent backups and process
  logs, Claude delegation output, phone uploads, GUI screenshots, scripts and live checks.

**Platform layer** (`src/platform/`)
- `Platform` interface with folders (via `platformdirs`), secrets and start-at-login;
  `WindowsPlatform` implements Credential Manager and the Startup-folder shortcut (COM).
- `src/core/credentials.py` and `src/core/startup.py` are thin facades over it, so callers did not
  change. The other Windows-only modules move behind the layer with the macOS port.

**Settings** (`src/core/config_store.py`, `config/defaults.json`)
- Merge order: shipped `config/defaults.json` ← the user's `config.json` ← environment overrides
  `JARVIS__<section>__<key>=<JSON value>` (e.g. `JARVIS__stt__device=cuda`; never saved).
- Settings writes only the keys that differ from the defaults (atomic write), so a new default
  reaches everyone who never changed that setting. The user file carries `config_version`;
  `migrate_user_config()` runs the `MIGRATIONS` chain at start-up.
- One loader for everything: every module that used to open `config/config.json` itself now goes
  through `config_store` (pipeline, STT, TTS, audio capture, router, controller, scripts).
- `defaults.json` is generic: no microphone index (null = system default), Whisper `small` on CPU,
  Piper voice, empty email identity, no default project, `full_access` and the phone server off,
  greeting prefix "Jarvis is back.". The old in-code `DEFAULTS` dict is gone.
- `audio.input_device_index` may be null again from Settings ("System default microphone").

**Profile** (`config/profile_defaults.json`)
- Shipped common apps (Notepad, Explorer, Calculator, plus Chrome/Edge only when installed),
  media keys and Windows system commands; the user's `user_profile.json` goes on top, section by
  section. `sleep` now asks for approval like shutdown and restart.
- An empty or missing profile works everywhere: no "About the user" in the prompt, greetings without
  a name, email refuses contact names but accepts addresses, workspace tools explain how to add a
  project folder, delegation says no project is set up.

**Fresh-install behaviour**
- Start-up validation only stops for what cannot degrade (the Claude Code CLI). A missing
  microphone, speech model or voice now means text-only or silent mode with a reported error,
  instead of the pipeline refusing to start.
- Whisper model names (`small`, `medium`, ...) download into `models/whisper` on first use; a
  folder path still loads a local model. Settings lists the names plus local model folders.
- The workspace policy protects the data, models and cache folders by path, wherever they are.

**Personal data removed**
- Email defaults, the greeting prefix, example strings (Chrome profile hint, TTS benchmark sentence,
  memory placeholder), the voice-environment docstring and the "on this PC" wording.
- Greetings: the Claude prompt is name-aware and gender-neutral; the local fallback drops the name
  cleanly when there is none.
- Android package id is the placeholder `com.example.jarvis` until the owner is decided.

**UI**
- Brand mark "J"; the orb's core wordmark is the plain name "JARVIS" (no dotted acronym).
- Settings subtitle, logging note, voice-folder note and the "Open data folder" button describe the
  new locations.

**Tests**
- `tests/__init__.py`: every run gets a throwaway `JARVIS_HOME` with a fake user "Alex"
  (`tests/fixtures/user_profile.test.json`, project folders created in a separate temp folder), and
  clears `JARVIS__*` overrides. Tests can no longer read or write a real user's files.
- New `tests/test_paths.py`: folders, model paths, platform layer, and a fresh install without any
  settings or profile (router, registry, prompt, workspace, delegation, validation).
- Rewritten `tests/test_config_store.py`: merge order, diff-only saves, new defaults reaching users,
  environment overrides, migrations, nullable microphone, generic defaults, profile merging and
  installed-only default apps.
- `tests/test_personal.py` uses the fixture user: project rules, a real temporary git repo for the
  commit-branch rule, email (including an empty profile) and neutral Chrome profiles.
- Greeting tests for profiles without a name; fixtures elsewhere use "Alex".
- `tests/test_agentic.py`: agent backups are looked up in the cache folder, and a new check proves the
  data, models and cache folders are protected even with full access.
- `tests/test_phone_server.py` imports `Path` itself (it used to borrow the server module's import).
- Temp folders are removed even when they hold read-only files (git marks its object files read-only
  on Windows, which used to leave `jarvis-tests-projects-*` folders behind).
- Fake keys in redaction tests carry `# export-scan: ignore`.

**Other**
- `requirements.txt`: `platformdirs`.
- `scripts/setup_piper.py` installs voices into `models/piper` and saves the choice through
  `config_store`; `scripts/benchmark_stt.py` compares the configured model with `small` and `medium`
  on the configured device.
- Docs and docstrings that named the old locations now describe the data folder:
  `docs/AGENTIC_TASKS.md`, `tools_ext/README.md`, `jarvis_gui.pyw`, `scripts/make_logo.py`.
- `ruff` is installed in the development venv for checks only; it is not a runtime dependency and goes
  into the `[dev]` extras when `pyproject.toml` is added.

**Housekeeping**
- Deleted the temporary copies of the personal settings and profile in `config/` (used only for the
  baseline test run below; gitignored, never committed). `config/` now holds only the shipped files.
- Deleted a stale `.cache/` in the repo, left by test runs of the code before this phase.

### Verified
- 208 automated tests pass (`python -m unittest discover -s tests -t .`, Python 3.12 venv).
- Phone app: `npx tsc --noEmit` clean, 16 Jest tests pass.
- Personal-data scan over the code (names, emails, local paths, the old product name): 0 hits;
  API-key scan: 0 hits.
- After the test runs: no `%APPDATA%\Jarvis` or `%LOCALAPPDATA%\Jarvis`, no `data/` or `.cache/`
  in the repo, no leftover temp folders.
- GUI smoke run with a scratch `JARVIS_HOME` and `JARVIS__gui__autostart_pipeline=false`: the app
  starts, writes only `jarvis.db` and `logs/jarvis.log` to the data folder, shows the defaults in
  Settings and the new wordmark, and quits cleanly (exit 0).
- `ruff check --select F` on the changed modules: clean (findings that predate this work, such as
  f-strings without placeholders, are left for the repo-wide lint pass).

### Decisions taken (review welcome)
- Whisper `small` on CPU as the shipped default; hardware detection (Phase 3) will pick the GPU and
  `medium` where it helps.
- Piper `en_US-amy-medium` as the default voice until the voice decision and the model downloader
  (Phase 3); until then `python scripts/setup_piper.py` installs it.
- The logo is still the crescent from the personal build; a Jarvis mark is a design task.

### Not done here (later phases)
- Model downloader, wizard, hardware detection, Kokoro/system voice (Phase 3). Silero VAD still
  comes from torch hub (downloaded on first start).
- Running the personal build and Jarvis side by side was checked in code (separate data folders,
  single-instance keys, shortcut names; a taken Ctrl+Shift+J is reported, not fatal), not live:
  the personal build was not running during the smoke test.

---

## 2026-10-23 · Phase 1 start: repository seeded and renamed to Jarvis

- Seeded from the personal build with the allowlist export (180 files); `.gitignore` in place
  (keeps the private planning notes out); `git init` without a commit; Python 3.12 venv.
- Baseline before any change: 192 tests passed. The code still read `config/config.json` and
  `config/user_profile.json` then, so the run used temporary, gitignored copies of the personal ones
  (full access and the phone server switched off in the copy); Phase 1 replaced and deleted them.
- Removed code that only served the personal build's earlier rename (database file rename,
  credential fallback, start-up shortcut clean-up that would have deleted `Jarvis.lnk`, old-name
  alias); 5 tests went with it.
- Renamed the product to Jarvis across code, UI, phone app and docs (451 mentions, 5 files
  renamed, e.g. `jarvis_gui.pyw`, `docs/AGENTIC_TASKS.md`); user-facing text says "orb", never
  "reactor".
- After the rename: 187 tests, phone app typecheck and 16 Jest tests passed.

---

## 2026-10-23 · Phase 0: plan review, naming decision, repository templates

Work on the private planning notes and on the templates that become this repo's public files.

- Reviewed the whole plan and fixed the places where its documents disagreed: the build folder path,
  zip distribution everywhere (Windows and macOS; macOS notarizes the app, then zips it again),
  "installer" wording, the setup wizard's screen order, when the platform layer is built (Phase 1:
  folders, secrets, start-up; the rest with the macOS port), the Settings page name ("AI"), the
  hardware rules for the speech model, the default greeting, and the update channel (the website's
  `latest.json`). Signing is required for public releases; the system voice engine is called
  `system` (SAPI on Windows, `say` on macOS); bug reports use Settings › About › Save a report.
- Recorded the name: the app and the assistant's default name are **Jarvis**; a name the user picks
  takes over everything they see or hear, while files, folders and credentials keep "Jarvis".
  Written only as "Jarvis" (no dotted acronym, no film references, "orb" rather than "reactor"), with
  a trademark check and a fallback name before going public.
- Templates for the public repo:
  - CI: the API-key scan stays in the workflow; the personal-data patterns live in a repository
    secret (`PERSONAL_DATA_PATTERNS`) so they are never published; hits print only `file:line`.
  - `.gitignore` excludes the planning notes and must be in place before `git init`.
  - Example settings gained `assistant.name`; the models manifest got ordered `recommend` rules that
    match the hardware table; README, CHANGELOG, CONTRIBUTING, SECURITY and the issue templates now
    match zip downloads and "Sign in with Claude", and use the new name.
- Checked: the templates' JSON and YAML parse, and the CI scan logic was exercised on sample files
  (keys and personal data caught, ignore markers respected, blank secret lines harmless).
