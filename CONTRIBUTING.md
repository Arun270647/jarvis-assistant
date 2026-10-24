# Contributing to Jarvis

Thanks for helping. Jarvis acts on people's PCs, so correctness and safety matter more than speed.

## Before you start

- **Bug fix:** open a pull request directly, and link the issue if there is one.
- **New feature, tool, provider or UI change:** open an issue first, so we can agree on the approach before
  you spend time on it.
- **Security problem:** don't open a public issue; see [SECURITY.md](SECURITY.md).

## Development setup

You need Windows 11, Python 3.11–3.13 (3.12 recommended) and Git.

```
git clone <this repository> jarvis-assistant
cd jarvis-assistant
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install --require-hashes -r requirements.lock      # the exact versions CI uses
pip install -e .[dev]                                   # adds ruff and pip-audit; the rest is already there
set JARVIS_HOME=%CD%\userdata                           # keep your dev data out of %APPDATA%
python -m unittest discover -s tests -t .
ruff check .
```

The tests need no GPU, microphone, network or API keys: they use the fakes in `tests/fakes.py`, in a
throwaway data folder. Files named `tests/live_*.py` are manual checks against real hardware and providers.

Phone app: `cd mobile && npm ci && npx tsc --noEmit && npx jest`.

## How the code is organised

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The rules that matter most:

- Layers import downward: `gui`/`remote` → `app` → `pipeline` → `agent` → `tools` → `router`/`memory` →
  `core`/`platform`.
- Every action on the PC is a tool executed through `PolicyExecutor.execute()`. Never perform side effects from
  the agent or the UI directly.
- Every subprocess goes through `src/core/winproc.py`; never `shell=True`. A test enforces it.
- Never touch Qt widgets from a worker thread; emit an event.
- Prefer a setting over a constant. Settings changes go through `config_store` validation; a change to the
  settings format comes with a migration in `config_store.MIGRATIONS`.
- No new databases. Schema changes are additive `MIGRATIONS` in `src/memory/store.py`.
- User-facing text in the web UI that names the assistant goes in `src/gui/web/strings.js`, with `{name}`.

## Adding a tool

1. Define a `ToolSpec` with a closed JSON schema, `risk`, default `permission`, `category` and a timeout, plus
   `approval_rows` for the approval dialog. Use `needs_confirmation` for arguments that should ask, and
   `always_ask` for cases that must never run without a click.
2. Put it in a group in `src/tools/groups.py` (a test fails otherwise), and think about which group's switch
   should control it.
3. Set `requires_task=True` if it changes the user's files, set `idempotent` correctly, and set
   `untrusted=True` on results that carry text from files, web pages, programs or screens.
4. Add tests (dry-run or fakes), and add the tool to [docs/TOOLS.md](docs/TOOLS.md). A test compares that page
   with the registry.

## Adding an AI provider

Prefer a preset for the OpenAI-compatible adapter (`llm.providers` in `config/defaults.json`). A new adapter
must pass the shared contract tests in `tests/test_tool_loop.py` and `tests/test_providers.py`. See
[docs/PROVIDERS.md](docs/PROVIDERS.md).

## Dependencies and licenses

Jarvis is Apache-2.0, so its own install can't include GPL code. An engine under the GPL becomes an add-on
that the app installs on request (`config/models.json` "addons", `src/core/addons.py`), like the Piper voice
engine. When you change `requirements.txt`:

```
uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes --no-header -o requirements.lock
python scripts/third_party_licenses.py
```

and keep the comment header at the top of `requirements.lock`. Running the compile against the existing lock
keeps every other version where it is; add `--upgrade-package <name>` to move one package.

Dependabot's weekly pull requests for Python packages change `requirements.txt` only, so their tests fail until
the lock is regenerated. Apply the new versions with the commands above in your own pull request. For the phone
app, update Expo's packages with `npx expo install --fix` in `mobile/` so they stay matched to the SDK.

## Pull requests

- One logical change per pull request, with tests.
- Sign off your commits (`git commit -s`) to certify the [Developer Certificate of
  Origin](https://developercertificate.org/): you wrote the change or have the right to submit it under the
  project's license.
- UI changes: include screenshots (`python jarvis_gui.pyw --screenshot <folder>` saves the running app's
  windows).
- Never commit personal data, API keys, recordings or databases. CI scans for common key formats.
- Write code in the style of the surrounding code. Comments explain why, not what.
- Note user-visible changes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
