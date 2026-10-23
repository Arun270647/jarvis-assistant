# Work log

What was built, how it was verified, and what was decided along the way. Newest first.
The phase plan and the release checklist live in the maintainer's planning notes (not in the repo);
each entry names the phase it belongs to.

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
