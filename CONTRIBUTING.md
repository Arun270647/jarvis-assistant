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
that the app installs on request (`config/models.json` "addons", `src/core/addons.py`), like the Kokoro and
Piper voice engines. When you change `requirements.txt`:

```
uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes --no-header -o requirements.lock
python scripts/third_party_licenses.py
```

and keep the comment header at the top of `requirements.lock`. Running the compile against the existing lock
keeps every other version where it is; add `--upgrade-package <name>` to move one package.

Dependabot's weekly pull requests for Python packages change `requirements.txt` only, so their tests fail until
the lock is regenerated. Apply the new versions with the commands above in your own pull request.

## Building the Windows installer

On Windows, with the Visual Studio C++ build tools (for `cl` and `rc`) and [Inno Setup 6](https://jrsoftware.org/isinfo.php)
installed (set `INNO_SETUP` to its folder if it isn't in the usual place), in the Python environment you develop
in:

```
python packaging/build_windows.py
```

It prepares `build\windows\Jarvis` and writes `dist\Jarvis-Setup-<version>-windows-x64.exe`, its `.sha256` file
and `latest.json`. The folder holds:

- `runtime`: python.org's embeddable Python (pinned with its SHA-256 in `packaging/runtime.json`), pip, and
  `requirements.lock` installed with hashes. Parts of Qt that Jarvis never loads are left out (the build follows
  the DLL imports of the Qt modules the app uses), and so are the packages' test suites and PyAV; the packages
  ship compiled only.
- `app`: the files from `git ls-files` that Jarvis reads at run time.
- `Jarvis.exe`, compiled from `packaging/windows/launcher.c`.

Before compiling the installer (`packaging/windows/jarvis.iss`), the build checks that the runtime imports every
package, that Qt WebEngine renders a page and that the app reports its version. `--check-install` then installs the
result silently and starts the installed copy (the release workflow does this on GitHub's throwaway machines; on
your own PC it registers a real install). `--reuse-runtime` skips the slow part when only the app changed. The
installer is about 150 MB.

## Building the Mac app

On a Mac with Apple silicon, with the Xcode command line tools (`xcode-select --install`) and Python 3.11+ with
`requirements.lock` installed:

```
python packaging/build_macos.py
```

It builds `build/macos/Jarvis.app` and writes `dist/Jarvis-<version>-macos-arm64.dmg` (the app and a link to
Applications on a compressed disk image), its `.sha256` file and
the `macos` entry of `dist/latest.json` (added to the Windows build's file when there is one). Inside the app:

- `Contents/Resources/runtime`: python-build-standalone's Python for Apple silicon (pinned with its SHA-256 in
  `packaging/runtime.json`), pip, and `requirements.lock` installed with hashes. Qt frameworks that Jarvis never
  loads are left out (the build follows `otool -L` from the Qt modules the app uses), and so is Tk.
- `Contents/Resources/app`: the files from `git ls-files` that Jarvis reads at run time, compiled to `.pyc`
  (the app never writes into itself, which would break its signature).
- `Contents/MacOS/Jarvis`, compiled from `packaging/macos/launcher.c`: it runs the app on the bundled Python
  inside its own process, so macOS treats Jarvis.app as the app for its permissions and Dock icon.

The build signs the app ad hoc (`codesign --sign -`); Jarvis has no Apple Developer Program membership, so it
isn't notarized. Before making the disk image it checks that the runtime imports every package, that Qt WebEngine
renders a page, that the launcher starts the app far enough to report its version, and that the signature
verifies. Then it mounts the image, checks the app in it again, and lets the updater's own code copy the app out
of it. Intel Macs aren't built: ONNX Runtime has no wheels for them.

## Releasing

1. Set the version in `src/version.py` and move the `[Unreleased]` notes in CHANGELOG.md under it.
2. Tag the commit `v<version>` and push the tag. The Release workflow builds the Windows installer and the Mac disk
   image on GitHub and drafts a release with both, their SHA-256 sums and one `latest.json`. Versions with
   letters (`0.1.0-alpha.1`) are marked as pre-releases.
3. Check the draft, download both files once, then publish it. Publishing copies its `latest.json` to the website
   (`.github/workflows/website-latest.yml`), which the download page and installed copies read.

To try the whole path without a tag, run **Actions › Release › Run workflow** with **rehearse** ticked: it drafts
a release named `rehearsal-<run number>`. Delete it afterwards (`gh release delete rehearsal-<n> --cleanup-tag
--yes`). Without **rehearse**, the run only builds the two files as downloadable artifacts.

Releases aren't code-signed: the release notes and the download page explain Windows SmartScreen and how to
check the SHA-256.

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
