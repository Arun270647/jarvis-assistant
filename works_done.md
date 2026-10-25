# Work log

What was built, how it was verified, and what was decided along the way. Newest first.
The phase plan and the release checklist live in the maintainer's planning notes (not in the repo);
each entry names the phase it belongs to.

---

## 2026-10-25 · Phase 9 and going public: the website, the repository and 0.1.0-alpha.1

The maintainer chose to make the repository public right away, so the website's download buttons work.
`0.1.0-alpha.1` is published as a pre-release.

### What changed

**The website** (`website/`, Next.js on Vercel): https://jarvis-assistant-oss.vercel.app
- **Home:** the app's HUD orb running a short demo: two tools allowed, and a restart that needs a click and is
  denied. Then how it works, what it can do, the ways to connect an AI, and the safety rules.
- **Download:** your system first. The buttons, size, version and SHA-256 come from `/latest.json`. Steps for
  the installer and the disk image, with the SmartScreen and "Open Anyway" notes and the hash commands.
- **Guide, privacy and safety, troubleshooting, changelog:** rendered at build time from the repository's own
  Markdown, so they never drift. Headings get GitHub's ids, because the app's ? buttons open `help.url` plus an
  anchor.
- **Look:** dark by default with a light theme. It works at phone width and respects reduced motion. Components
  are adapted from Aceternity UI and 21st.dev.
- **`/latest.json`** is a static file. `website-latest.yml` commits each published release's copy, and the
  download page and installed copies read it.
- The project is called `jarvis-assistant-oss`. Two Vercel addresses turned out to be taken
  (`jarvis-assistant` and `jarvis-desktop-assistant`): the first tag of the alpha pointed the app at the second,
  so it was cancelled and the tag moved to the commit with the right address.
- Vercel's "ignored build step" compares with the last deployed commit (`VERCEL_GIT_PREVIOUS_SHA`). Comparing
  only with the commit before skipped the first production build.

**The repository is public**
- Before the switch, every file version in the history (571 in 29 commits) was scanned for personal data and
  keys. Nothing turned up apart from the fake keys in the redaction tests.
- GitHub's private vulnerability reporting is on (SECURITY.md points to it).
- The personal-data scan now allows exactly the public account name, which appears in release and repository
  links. CI's copy of the patterns is a repository secret that the maintainer updates.

**0.1.0-alpha.1**
- Built by the Release workflow: the Windows installer (148 MB) and the Mac disk image (421 MB), with their
  SHA-256 sums and one `latest.json`.
- Before the tag, two rehearsals ran on GitHub. The first caught a build bug that a reused local runtime had
  hidden (a Qt file matched two clean-up patterns and was deleted twice). In the second, the installer installed
  silently on a clean Windows machine, the installed copy reported its version and Settings › Apps listed it.
  The disk image mounted, its app reported its version, and the updater copied it out intact.

### How it was checked
- The website builds and lints cleanly (every page static). It was viewed at desktop and phone widths in both
  themes. The guide has the anchors the app's ? buttons use.
- After the switch, the repository page and the website answer signed-out.

---

## 2026-10-25 · Releases: a Windows installer instead of the zip, smaller downloads, 0.1.0-alpha.1

The maintainer asked for a plain .exe and .dmg to download, with no zip to extract. The Mac download already
was a DMG; the Windows one is now an installer.

### What changed

**The Windows installer** (`packaging/windows/jarvis.iss`, compiled by `packaging/build_windows.py` with Inno Setup 6)
- `Jarvis-Setup-<version>-windows-x64.exe` installs for the current user only, in
  `%LOCALAPPDATA%\Programs\Jarvis`, without administrator rights. It adds the Start Menu shortcut (and a Desktop
  one if asked) and the Settings › Apps entry, then opens Jarvis.
- It installs the same folder as before (launcher, runtime, app), packed as one solid LZMA2 archive.
- The installer adds no uninstaller of its own. The Apps entry runs `Jarvis.exe --uninstall`, so Jarvis's own
  uninstall (with "delete my data") stays the only one. The shortcut names and the Apps key are the ones
  `src/app/install.py` and `uninstall.py` already use.
- **Updates** download the next installer, check its SHA-256 and keep it with a note of its version. On
  **Restart to update**, a hidden script waits for Jarvis to quit, runs the installer silently into the same
  folder and starts Jarvis again. The installer replaces `app` and `runtime` whole, so nothing of the old
  version lingers.
- The folder-swap updater (`packaging/windows/updater.c`) and the zip are gone. Install for me stays, for a
  Jarvis folder run from somewhere other than the usual one.

**Smaller downloads** (`slim()`, shared by both builds)
- The third-party packages ship compiled only: a `.pyc` in place of each `.py`, with no `__pycache__` folders.
  pip and Qt's helpers keep their sources.
- Also left out: the packages' own test suites, ONNX Runtime's model-conversion tools, the Hugging Face download
  accelerator, Pythonwin and the PyWin32 help file, and Qt's developer-tools pages.
- **PyAV goes, with its FFmpeg.** faster-whisper imports it only to read audio files, and Jarvis hands it audio
  arrays.
  - Its wheels bundle the GPL encoders x264 and x265, which the previous downloads carried by accident: Jarvis is
    Apache-2.0 and ships nothing GPL.
  - A stand-in module keeps faster-whisper's import working. THIRD_PARTY_LICENSES.md says what the downloads
    leave out.

| Windows download | Before (zip) | Now (installer) |
|---|---|---|
| Download size | 300 MB | 148 MB |
| Installed size | 752 MB | 586 MB |
| Files | 16,549 | 8,135 |
| Folders | 1,458 | 811 |

**Release and website wiring**
- The Release workflow builds the installer instead of the zip. On GitHub's throwaway Windows machine it also
  installs the result silently and starts the installed copy.
- A new workflow (`website-latest.yml`) copies each published release's `latest.json` to the website.
- The app's `updates.url` and `help.url` now point at https://jarvis-assistant-oss.vercel.app.
- The version is `0.1.0-alpha.1`.

### How it was checked
- The full suite, 462 tests. New ones cover:
  - `slim()` run for real on a fake site-packages: what goes, compiled-only modules import, and the PyAV
    stand-in imports but offers nothing;
  - the installer script's promises (per-user, no uninstaller of its own, the same shortcut and Apps entry,
    `app` and `runtime` replaced on update);
  - the Windows update path (the download must be a program, the version note, the hidden script's order of
    waiting, installing silently and restarting).
- Ruff is clean, and so is the license list.
- A local build reusing the runtime: the import, Qt WebEngine and version checks passed, and Inno Setup made the
  148 MB installer.
- The slimmed runtime downloaded the tiny Whisper model (without the accelerator, over plain HTTP) and
  transcribed audio with voice detection.
- The silent install runs in the release workflow. Installing it by hand on a clean PC is part of the testing
  phase.

---

## 2026-10-25 · Phase 7: macOS, and releases with a Windows zip and a Mac DMG

Jarvis now runs on Macs with Apple silicon (macOS 13 or later). Everything was built and tested with fakes on
Windows; the parts that need a real Mac run in CI (the test suite on macos-latest, the app build on macos-15)
and in the maintainer's testing phase.

### What changed

**Platform layer.** The remaining Windows-only code moved behind `src/platform/`:
- `src/platform/macos.py`: the Keychain (through keyring), start at login as a LaunchAgent that opens
  Jarvis.app, `open` for files and links, the Privacy & Security pages, and live permission states (microphone,
  Accessibility, Screen Recording).
- `src/gui/native.py`: the global shortcut (Carbon's RegisterEventHotKey, ⌃⇧J, no permission needed), bringing
  the window to the front, the dark title bar. The tray icon is the menu bar icon on a Mac.
- Desktop control: `desktop_win.py` (UI Automation) and `desktop_mac.py` (the Accessibility API, Quartz events,
  the window server's window list) behind the same tools and safety rules in `desktop_tools.py`.
- Terminals: `terminal_mac.py` opens Terminal.app windows, reads them, types lines (`do script`), and follows
  the same typed-line rules, with zsh's own added.
- The system voice uses `say` on a Mac. Apps come from /Applications; media keys and system commands have Mac
  defaults (`config/profile_defaults.macos.json`). macOS-only packages (PyObjC, keyring) and Windows-only ones
  (pywin32, uiautomation) carry platform markers in `requirements.txt` and `requirements.lock`.

**Setup wizard.** On a Mac, the microphone screen and the "What Jarvis may do" screen show each permission's
state live (allowed, not asked yet, not allowed), with **Allow** or **Open System Settings**.

**Jarvis.app** (`packaging/build_macos.py`, `packaging/macos/launcher.c`)
- python-build-standalone's Python 3.13 for Apple silicon (pinned by SHA-256 in `packaging/runtime.json`),
  pip, and `requirements.lock` installed with hashes. Qt frameworks the app never loads and Tk are left out.
- The launcher runs the app on the bundled Python inside its own process, so macOS treats Jarvis.app as the app
  for permissions and the Dock. Everything is compiled to `.pyc` ahead of time, because writing into the app
  would break its signature.
- Signed ad hoc (`codesign --sign -`), not notarized: the first start needs System Settings › Privacy &
  Security › Open Anyway. The docs, the release notes and the download page explain it.

**The Mac download is a DMG** (`Jarvis-<version>-macos-arm64.dmg`): Jarvis.app and a link to Applications on a
compressed, read-only disk image (lzfse).
- **Updates** download the next DMG, check its SHA-256, attach it read-only where Finder doesn't show it, copy
  the app out with `ditto` (which keeps the signature) and detach it. The same swap script as before installs
  it on restart.
- **Install for me**, started from the mounted DMG, copies Jarvis into Applications, then ejects the image and
  deletes the `.dmg`.
- After building the image, the build mounts it and checks the app inside (signature, version). Then it runs the
  updater's own `stage()` on the image, so the update path is checked on a real Mac before every release.
  `hdiutil` is tried again on "Resource busy", which GitHub's Macs sometimes report.

**Releases** (`.github/workflows/release.yml`)
- A `v*` tag (it must match `src/version.py`) builds the Windows zip and the Mac DMG at the same time. It then
  drafts one GitHub Release with both files, their SHA-256 sums, one `latest.json` and install notes for each
  system.
- Versions with letters (`0.1.0-alpha.1`) are marked as pre-releases.
- Run by hand, the workflow builds both files as artifacts. With **rehearse** ticked, it also drafts a
  `rehearsal-<run number>` release, to check the whole path without a tag.
- CI can now be started by hand too (Actions › CI › Run workflow), so a branch gets the Mac tests before a pull
  request.

### How it was checked
- The full suite on Windows, 456 tests (the new Mac tests run here with fakes: the Keychain, the LaunchAgent, key
  parsing, zsh rules, Terminal tabs, Applications discovery, the app layout, the DMG update and Install for me
  with `hdiutil` faked, the swap script under `sh`, the build's helpers). Ruff is clean, and so is the
  third-party license list.
- The release workflow's version step and release step ran locally against fake files and a fake `gh`.
- On GitHub (run by hand on the branch): CI passed on Windows (Python 3.11 to 3.13) and, for the first time,
  on macos-latest (Python 3.13), with the data scan and pip-audit. The release rehearsal passed:
  - the Windows zip: 300 MB, 4 minutes;
  - the Mac app: 1.4 GB unpacked, its DMG 484 MB, 6 minutes on macos-15. The image mounted, its app reported
    its version, and the updater's `stage()` copied the app out with a valid signature. `hdiutil` needed no
    retries;
  - a draft `rehearsal-3` release with both files, their SHA-256 sums and one `latest.json` listing both,
    marked as a pre-release. No tag was created, because drafts get their tag only when published.

### Not done here
- Nothing has run on a real Mac outside CI yet. A clean Mac, the speech-model benchmark on Apple silicon, and
  the Kokoro add-on on a Mac are in the testing phase.
- No Intel build: ONNX Runtime has no wheels for Intel Macs.

---

## 2026-10-24 · Phase 6: the Windows zip, updates and Install for me

The maintainer's decisions for this phase: no code signing (Jarvis is free and has no budget for it), and all
live testing waits for a separate phase after everything is built.

### What changed

**The Windows zip** (`packaging/build_windows.py`, about 300 MB, 752 MB unpacked)
- `runtime/`: python.org's embeddable Python 3.13.16 and pip 26.2.1, both pinned by SHA-256 in
  `packaging/runtime.json`. `requirements.lock` is installed with its hashes and wheels only. The runtime's
  `python313._pth` is its whole module search path (standard library, its own site-packages, `..\app`), so
  nothing installed on the PC leaks in. pip stays, because the Kokoro and Piper engines are pip-installed at run
  time.
- `app/`: only the tracked files the app reads at run time (code, shipped settings, the getting-started guide).
- Pruning took the folder from 1.4 GB to 752 MB:
  - Qt: only the DLLs that the app's seven Qt modules import, followed through their import tables (the build
    reads the PE headers itself), plus the few that Qt loads at run time. Qt's tools, QML, debug resources,
    translations other than WebEngine's English, and type stubs are left out.
  - The Agent SDK's bundled copy of Claude Code (298 MB): the wizard installs Claude Code with Anthropic's
    official installer, so Jarvis doesn't redistribute Anthropic's program.
- Before zipping, the build:
  - runs the updater's tests;
  - checks that the runtime imports every core package from its own folders;
  - renders a page in Qt WebEngine offscreen (this catches a DLL pruned by mistake);
  - checks that the app reports its version.
  The zip is reproducible: same commit, same file times. The build also writes the `.sha256` file and
  `latest.json`.
- **`Jarvis.exe`** (`packaging/windows/launcher.c`, 0.1 MB) starts `runtime\pythonw.exe app\jarvis_gui.pyw`.
  When started from inside a zip or a temporary folder, it shows "Please extract the zip first" with the steps.
- Both programs embed a manifest with `asInvoker`. Without it, Windows takes a program called "…Updater" for an
  installer and asks for administrator rights.

**Updates** (`src/app/updates.py`, `packaging/windows/updater.c`)
- At start (and from Settings › About), a newer version in `latest.json` is downloaded in the background,
  checked against its SHA-256 and unpacked into `Jarvis\.update` (unsafe paths in the zip are refused). A bar
  then offers **Restart to update**.
- **`Jarvis-Updater.exe`** runs from a copy in the temporary folder. It waits for Jarvis to quit, then moves each
  item of the new version into place and keeps the old one in `Jarvis\.old`; retries cover antivirus scans. If a
  move fails, it puts everything back. Then it starts Jarvis, which shows "updated to version …" or why it
  failed, and deletes `.old`.
- A downloaded update that hasn't been installed yet is kept and offered again at the next start.
- Run from source, or from a folder Jarvis can't write to, a new version is offered as a download, as before.

**Install for me** (`src/app/install.py`): on the wizard's last screen (**Install for me** / **Keep it here**)
and in Settings › About.
- Copies the folder to `%LOCALAPPDATA%\Programs\Jarvis` without administrator rights.
- Adds a Start Menu shortcut (and a Desktop one if asked) and an entry in Settings › Apps, whose Uninstall opens
  Settings › About (`Jarvis.exe --uninstall`).
- Points the start-up shortcut at the new copy, starts that copy once this one has quit, and deletes the
  extracted folder.
- It never overwrites a folder that isn't a Jarvis folder.

**Uninstall and start-up**: shortcuts start `Jarvis.exe` in the zip (pythonw and the script from source).
Uninstall deletes the whole Jarvis folder of the zip after exit, and only a folder that has Jarvis.exe, runtime
and app in it.

**Release workflow** (`.github/workflows/release.yml`)
- A `v*` tag must match `src/version.py`. The workflow builds the zip on GitHub and drafts a release with the
  zip, its SHA-256 and `latest.json`.
- The release notes explain SmartScreen and how to check the SHA-256. Build provenance is attested once the
  repository is public (on private repositories it needs a paid plan).
- Run by hand, the workflow only builds the zip as an artifact.

**Download page** (`website/`): a static template.
- It reads `latest.json` and shows the download button, size and SHA-256.
- It covers the three steps, the "Windows protected your PC" warning, and how to check the download.
- Visitors on other systems see a note. It follows the light or dark theme and works at phone width.

**Link check** (`scripts/check_links.py`) covers model downloads (and their sizes), model pages, licenses and
samples, add-on versions on PyPI, the build's pinned downloads, the providers' API addresses and the links in the
code.

**Docs**:
- INSTALL: install from the zip, SmartScreen, the SHA-256 check, Install for me and updates.
- GETTING_STARTED: step 1. TROUBLESHOOTING: three new entries. ARCHITECTURE: the zip's parts.
- CONTRIBUTING: building the zip and releasing. CHANGELOG.
- NOTICE and THIRD_PARTY_LICENSES now list the bundled Python and pip.
- `jarvis_gui.pyw --version` prints the version.

### Verified
- All 421 automated tests pass (21 new):
  - unpacking an update (and refusing unsafe or incomplete zips), keeping it across restarts, reporting the
    result, and the updater's command line;
  - Install for me, with copies, shortcuts, the Apps entry and the relaunch script, and that it never overwrites
    a folder that isn't Jarvis's;
  - the start-up shortcut and uninstall in the zip layout;
  - the PE import reader, and the link checker against a fake server.
- The compiled updater, run for real on a fake Jarvis folder:
  - it swaps in the new version and keeps the old one;
  - with a file held open, it puts everything back and says so;
  - an incomplete update is refused.
  The build runs these too.
- The real packaged app, end to end in a scratch folder:
  - `Jarvis.exe` started it on its own Python 3.13.16;
  - with version 0.1.1 waiting, the bar offered Restart to update;
  - clicking it quit the app, the updater swapped the folders and started 0.1.1, the bar said "updated to
    version 0.1.1", and `.old` and `.update` were gone.
- The launcher, started from a folder like the one Explorer uses for a program inside a zip, showed "Please
  extract the zip first" and started no Python.
- The packaged runtime's pip installed the Kokoro engine add-on (6 seconds), and Kokoro spoke from it.
- The link check: 86 links, none broken. The download page renders with the real `latest.json`, wide and at
  phone width.

### Not done here (Phase 8, the maintainer's testing)
- Install for me for real (it creates a Start Menu shortcut, an Apps entry and a folder in the user's profile):
  covered by tests with mocks.
- The zip on a clean Windows 11 PC or VM, with and without an NVIDIA card, including SmartScreen.

### Decided (review welcome)
- python.org's embeddable Python rather than a frozen app (PyInstaller): add-ons need a real Python with pip, and
  unsigned frozen apps are often flagged by antivirus.
- Python 3.13.16 rather than 3.13.17, which was three days old.
- Small C programs for the launcher and the updater, compiled with the Visual Studio build tools (GitHub's
  Windows runners have them).
- The release workflow drafts the release; publishing stays a person's step.

---

## 2026-10-24 · Kokoro is the default voice

The maintainer chose Kokoro as the default voice. Jarvis stays Apache-2.0.

### What changed
- **Kokoro engine** (`src/tts/kokoro.py`, `tts.engine = "kokoro"`): Kokoro-82M through kokoro-onnx on ONNX Runtime,
  on the CPU. The voice's first letter picks espeak-ng's accent (a: American, b: British). Replies are voiced a
  sentence at a time, and the next sentence is made while the current one plays. Stopping drops the sentences not
  played yet.
- **Licensing: the engine is an add-on.** kokoro-onnx is MIT and the model is Apache-2.0, but the engine needs
  phonemizer (GPL-3.0-or-later) and espeakng-loader, which bundles espeak-ng (GPL-3.0). Installing kokoro-onnx with
  pip pulls both in, so the whole engine is an add-on, like Piper's: `config/models.json` "addons" › `kokoro` (six
  pinned packages, about 22 MB). The downloader installs it from PyPI into `models/addons/kokoro` with the first
  Kokoro voice. Nothing GPL is in `requirements.txt` or the lock file, and a test checks both.
- **The model file is the full-precision one** (`kokoro-v1.0.onnx`, 325 MB, plus the 28 MB voices file, from the
  kokoro-onnx release `model-files-v1.1`, pinned by SHA-256). Measured on an i7-12700H with ONNX Runtime 1.31:
  - full precision: 3.7× real time when the PC was quiet, 1.2–2× with other programs busy; "Hi, I'm Jarvis." took
    0.4–1.7 s;
  - the int8 file (114 MB): 0.7× real time;
  - the fp16 file (164 MB): about 1× real time.

  The smaller files are slower than real time on the CPU, so they aren't offered.
- **Catalogue and wizard**: four Kokoro voices (Heart and Michael, US; Emma and George, UK) share one download.
  The wizard recommends Kokoro · Heart to everyone. Voice cards say "downloaded" once a voice is on disk, or that
  one download covers several voices. ▶ on a downloaded voice now says "Hi, I'm {name}" in that voice; a voice
  that isn't downloaded yet plays its online sample if it has one (Kokoro has one, for Heart).
- **Settings**: the defaults are `tts.engine = "kokoro"` with the voice `af_heart`. Settings › Voice adds the
  Kokoro voice (every voice in the downloaded file) and its speed (0.5–2). Chatterbox's fallback voice can now be
  any engine, and it is Kokoro by default (`tts.chatterbox.fallback`). Until Kokoro is downloaded, the Windows
  voice speaks, as before.
- `src/tts`: one `build_engine()` for all engines, `preview_engine()` for hearing a voice before choosing it, and
  `split_sentences()` shared by Kokoro and Chatterbox. Diagnostics list the add-on packages and the voice in use.
- Docs: README, INSTALL, GETTING_STARTED (sizes), TROUBLESHOOTING, ARCHITECTURE, CONTRIBUTING, NOTICE,
  THIRD_PARTY_LICENSES (the add-on row and the model's license) and CHANGELOG. `pyproject.toml` has a `kokoro`
  extra for developers (`pip install -e .[kokoro]`).

### Verified
- All 400 automated tests pass (12 new). They use a stand-in for kokoro-onnx and cover the accent, the voices file,
  the error when the voice or its engine is missing, the fallback to the Windows voice, speaking in order, stop,
  previews, Chatterbox's fallback, the add-on in the catalogue and the defaults matching the catalogue's files. The
  suite ran with Kokoro's packages uninstalled, as in CI. Ruff is clean, and the third-party list is complete.
- End to end on Python 3.13, in an environment that had never had kokoro-onnx:
  - the voice files went in through "I already have this file", and their checksums were checked;
  - the downloader installed the engine add-on from PyPI in 6 seconds (21.7 MB, with the packages' license
    files);
  - the app's engine factory loaded Kokoro from the add-on folder and synthesized speech; a preview of a British
    voice worked too.
- The setup wizard's voice step, Settings › Voice and Settings › Models, checked in screenshots from a scratch data
  folder. The engine names in Settings › Voice were shortened because they were cut off.

### Decided (review welcome)
- The full-precision model, because the smaller files are slower than real time on the CPU (numbers above).
- Four Kokoro voices in the wizard (the best-rated American and British voices, one female and one male each);
  Settings › Voice lists all the others in the file.
- No int8/fp16 choice in the catalogue for now. A GPU build of ONNX Runtime could change that later.

---

## 2026-10-24 · Desktop only: the phone app is removed

The maintainer decided that people get the Windows and Mac app only, without a phone companion.

### What changed
- Removed the Android app (`mobile/`), the desktop's phone server (`src/remote/`), pairing, Settings › Phone
  app, the phone parts of the pipeline (commands from another device, voice notes, replies kept off the
  speakers, longer approval timers), the prompt's phone context, `docs/PHONE_APP.md`, the CI job for the app,
  Dependabot's npm updates and the app-icon script.
- `aiohttp` and `segno` were only used by the phone server and left the requirements. The lock file lost
  them and the five packages only they needed.
- Existing installs are cleaned up:
  - settings version 5 drops the `phone` section from the user's settings file;
  - the database drops the paired-phones table at start-up, which held token fingerprints.

### Verified
- All 388 tests pass, after removing the phone tests and adding two new ones: the settings migration and
  dropping the table from an old database. Ruff is clean.
- The tests ran with aiohttp and segno uninstalled, so nothing still imports them.
- Every script of the web UI parses.

---

## 2026-10-24 · Phase 5: license, repository files, documentation and CI

### What changed

**License: Apache-2.0** (decided by the maintainer's delegation, following the plan's recommendation)
- `LICENSE` (the official text, checked by SHA-256 in a test), `NOTICE` (copyright "the Jarvis authors"; Qt
  under the LGPL, the fonts, optional add-ons), `src/gui/web/fonts/OFL.txt` (Barlow, SIL OFL 1.1).
- `THIRD_PARTY_LICENSES.md`, generated by `scripts/third_party_licenses.py` from the installed packages'
  metadata and the phone app's lock file. No core dependency is GPL-only. PySide6 is used under its LGPL
  option.
- The phone app was carrying Expo's template license as `mobile/LICENSE`. It is kept as
  `mobile/LICENSE-expo-template.txt`, for the template parts, and the app is marked Apache-2.0.

**The Piper voice engine becomes an add-on.** piper-tts is GPL-3.0-or-later, so it left `requirements.txt`.
- `config/models.json` gained an `addons` section, and every Piper voice names the add-on it needs.
- Choosing a Piper voice in the wizard or Settings › Models now also installs the engine from PyPI. It goes into
  `models/addons/piper` (`src/core/addons.py`, pip `--target --no-deps`), which is put on `sys.path` only when
  the voice loads.
- The downloads show "Installing the Piper voice engine…". A voice whose engine is missing shows "Needs its
  engine: click Download".
- `scripts/setup_piper.py --engine` installs just the engine. A developer's own `pip install piper-tts` still
  counts as installed.
- Jarvis never ships the engine, so the Apache-2.0 distribution contains no GPL code, and the user gets the
  engine directly from PyPI.

**Build and lint**
- `pyproject.toml`: metadata (license expression, Python ≥ 3.11), dependencies read from `requirements.txt`,
  extras `dev` and `piper`, and the ruff settings.
- `requirements.lock`: exact versions with hashes for every platform (uv, universal), used by CI.
- Ruff now passes on the whole repository with the default rule set. Lambdas assigned to names and a variable
  called `l` are allowed, because the code uses them on purpose. 28 unused imports, unused variables and
  f-strings without placeholders were fixed.

**CI** (`.github/`)
- Tests on Windows with Python 3.11, 3.12 and 3.13, installed from the lock file. The step that would have
  skipped the Silero tests now downloads the pinned speech model with its checksum.
- Also: ruff, the third-party list check, a key-format scan, the personal-data scan (from the repository secret
  `PERSONAL_DATA_PATTERNS`, so the patterns never appear in the public repo), the phone app's type check and
  tests, and pip-audit of the locked versions.
- CodeQL for Python and JavaScript/TypeScript, which starts once the repository is public (code scanning needs
  paid Advanced Security on private repositories). Dependabot for pip, npm and GitHub Actions.
- Issue templates (bug, feature) and a pull request template.

**Docs**: `docs/INSTALL.md`, `ARCHITECTURE.md`, `TOOLS.md` (every tool with its group, risk, default and when
it asks), `PHONE_APP.md`, `EXTENSIONS.md` and `TROUBLESHOOTING.md` (every error bar). Also `CONTRIBUTING.md`
(setup, rules, adding tools and providers, the DCO), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1) and
`CHANGELOG.md`. The README gained a documentation table and the license section, and its status note no longer
says there's no setup wizard.

**Layering**: Phase 4 had made `config_store` import from `src/tools`. The v3→v4 migration now carries its own
frozen group lists (a migration must not change when the code does), and validation reads the switchable
groups from the defaults.

### Verified
- 407 automated tests pass (13 new):
  - the license text, NOTICE and font license;
  - no GPL package in `requirements.txt` or among the core dependencies;
  - every requirement in the third-party list;
  - the add-on's pip command and failure message, its `sys.path` handling, and installing it after a voice's
    files (or failing in plain words);
  - `docs/TOOLS.md` against the real registry, tool by tool;
  - every relative link and heading anchor in the Markdown files (58 links).
- Python 3.11: all 407 tests pass in a fresh environment without Piper. The CI step that downloads the speech
  model was run locally, and the four speech-detection tests pass with it.
- Python 3.13: the lock file installs with pip and `--require-hashes`, which is CI's exact install, and all 407
  tests pass with nothing skipped.
- The Piper add-on for real, on 3.13 where Piper had never been installed: `addons.install` fetched piper-tts
  1.8.0 and pathvalidate from PyPI into a scratch models folder (47 MB, with Piper's own GPL `COPYING`). The
  voice's status went from missing to installed, Piper loaded from the add-on folder, and it spoke 2.4 seconds
  of audio.
- The wheel builds, with `License-Expression: Apache-2.0` and the dependencies from `requirements.txt`.
- Ruff clean. pip-audit: no known vulnerabilities in the locked versions. The key-format scan finds nothing
  unmarked.
- The grep version of the personal-data patterns matches exactly what the Python scan matches (same counts per
  pattern), and finds nothing in the repository.

### Decided (review welcome)
- No npm audit in CI: npm counts Expo's build tools (`@expo/cli`, node-forge, no fixed version) as app code, so
  it would fail on every run. Dependabot covers the phone app's packages.
- No repository URL in committed files (the owner question is still open, and the URL would trip the
  personal-data scan). Clone instructions say `<this repository>`.
- Code of Conduct reports go through GitHub's private reporting until there's a project email address.

### Follow-up after the first CI run on GitHub
- The repository secret `PERSONAL_DATA_PATTERNS` is set, and the personal-data scan passed on GitHub.
- `src/gui/web/ds/styles.css` (the "Industry" design-system tokens) is the project's own work, so it ships
  under Apache-2.0.
- The first run failed four tests on every Python version. They were test bugs, not app bugs:
  - The runner's temp folder is an 8.3 short name (`RUNNER~1`). Two tests compared a path the app had kept in
    that form with a fully resolved one. The folder check itself resolves both sides, so it was never fooled.
    Both tests now compare resolved paths. The failure was reproduced locally with a short-name TEMP, and the
    full suite passes under it.
  - A registry test only passed on a PC with Chrome profiles. It now brings a fake profile.
  - A phone-command test sent its next command right after the reply event, before the assistant was idle
    again, and got "queued" instead of "sent" on a slow runner. It now waits for idle first.
- Dependabot's first run opened 13 pull requests, one per package. They were applied by hand in one commit and
  closed:
  - `sounddevice` 0.5.1 → 0.5.6, with `requirements.lock` regenerated so only that package moved. Dependabot
    edits `requirements.txt` but not the lock CI installs from, so its pull request would have tested the old
    version. `tests/test_requirements_lock.py` now fails when the two disagree. Recording from the microphone
    with 0.5.6 was checked on a real PC.
  - The four pull requests that only raised minimum versions (aiohttp, pywin32, setuptools, pip-audit) were not
    applied, because the code doesn't need the newer versions.
  - Phone app: `npx expo install --fix` aligned every Expo SDK 58 package (11 patch releases, more than
    Dependabot found). React stays at 19.3.0, the version Expo SDK 58 pins. Type check and tests pass.
  - CI: `actions/checkout`, `setup-python` and `setup-node` v7.
  - `dependabot.yml` now opens one grouped pull request per ecosystem and only raises minimums when necessary.
    It leaves out React and major versions, because Expo SDK upgrades go through `expo install`.
- GitGuardian flagged three "secrets" in `tests/test_security.py`. They are the made-up keys and bearer
  tokens the redaction tests use. No real credential was committed.

---

## 2026-10-24 · Phase 4: safe defaults and hardening

### What changed

**Tool groups** (`src/tools/groups.py`, `tools.groups`)
- Every built-in tool belongs to one group: Basics (always on), Read your project folders (on), Change your
  project folders, Command Prompts, Control other apps (all off), Lock and power (on), Email and Hidden
  coding agent runs (off). A tool whose group is off is not registered, so the AI never sees it. Extension
  tools have no group: they load only when ticked.
- The setup wizard's Everyday / Work / Full control choices now switch groups instead of writing per-tool
  denies.
- Settings › Tools and permissions: a switch per group with what it covers. The tool table has a Group
  column, and tools in groups that are off are dimmed.
- Settings version 4. An existing settings file keeps what it could do: the wizard's earlier choice becomes
  groups (its denies go), and without one every group stays on.

**Instructions hidden in content** (the untrusted-content rule, `tools.untrusted_asks`, on by default)
- Once a tool result in a request is untrusted, tools that can change something (sensitive or destructive,
  and not a read) ask first for the rest of that request:
  - after a web page, a screen or another outside source, all of them;
  - after the user's own project files, command output or project Command Prompts, all but the project tools.
    Edits inside the project are confined and backed up, and whoever wrote those files can already run code
    through the project's own commands.
- The approval dialog says why it asked and names what was read. Screenshots now count as untrusted too.

**Approvals that need a click or key** (`ToolSpec.always_ask`)
- These always ask, even when Settings says allow, and a spoken yes is refused for them: deleting a file,
  email to anyone outside the trusted list, shutting down or restarting (by key or by command, whatever the
  profile says), and typing into a program running in a Command Prompt.
- A spoken no still denies. The broker itself refuses a spoken yes, so a new front-end can't get around it.
- Jarvis says "To allow it, click Allow or press Y." and the dialog explains.
- `voice_approval.high_risk` turns spoken yes back on. Settings never offers "allow" for deleting files.
- Permission and approval settings now reach the running assistant at the next tool call, without a restart.

**Secrets**
- More key patterns are masked: OpenAI `sk-proj-` / `sk-svcacct-` / `sk-admin-`, OpenRouter, Groq, xAI,
  Hugging Face, Perplexity, Cerebras, Replicate, NVIDIA, `Authorization` headers, bearer tokens and passwords
  in URLs. Prefixed keys must have a digit and stand on their own, so ordinary hyphenated words are left
  alone.
- The log file and the log listeners now mask every line, whoever wrote it. The tool log line prints the
  masked arguments.

**Phone app**: still off by default. An address with 5 failed hellos or pairings within a minute is locked
out for 5 minutes. A new pairing shows a desktop notification ("If that wasn't you, remove it in Settings ›
Phone app") and a line in the activity feed.

**Desktop control**: refuses password managers (1Password, Bitwarden, KeePass/KeePassXC, LastPass, Dashlane,
NordPass, Keeper, RoboForm, Enpass, Proton Pass, Password Safe), and windows whose title shows a browser's
saved passwords or payment methods, or Windows Credential Manager (`desktop.blocked_titles`).

**Extensions**: a Settings section lists the files in `tools_ext/` with a switch each, under a warning that
they run with full user rights and nobody reviewed them.

**Docs**: `docs/SECURITY_MODEL.md` ("What Jarvis can do on your PC, and how to limit it") and `SECURITY.md`
(private reporting through GitHub, response targets, scope). The user guide and README point to them.

### Verified
- 394 automated tests pass (28 new):
  - every built-in tool is in exactly one group, and the defaults hide the risky ones;
  - the migration's three cases;
  - the untrusted-content rule at both levels, including the level only ever rising and each request
    starting clean;
  - click-only approvals: a spoken yes refused, a click accepted, a spoken no denies, the setting;
  - shutdown under a custom command name, email to strangers;
  - every key pattern, and ordinary words left alone;
  - keys absent from events, the printed log line and the audit trail;
  - the log filter;
  - the desktop block list;
  - phone lockout and the pairing notification;
  - the security page naming every group.
- Static test: what `credentials.read` returns (directly, or through `api_key()` / `get_password()`) is never
  passed to print, a logger or an event emitter, and a new function that reads credentials fails the test
  until it is reviewed. Its detector was checked against code that leaks, and code that doesn't.
- Live in a scratch data folder: Settings › Tools and permissions showed the groups with the safe defaults.
  Tools in groups that are off were dimmed, delete_file offered only ask / deny, and the Extensions warning
  rendered.
- Phone app: TypeScript clean, 16 tests pass. Its protocol type carries the two new approval fields.
- Personal-data scan over every changed or new file: 0 hits.

### Fixed along the way
- A flaky phone test (`test_revoked_phone_is_disconnected`, about 1 run in 75) was a test-client bug. The
  server sent the right close code (4403, "removed"), but aiohttp's automatic close reply sometimes failed on
  the already-dropped connection and overwrote the recorded code with 1006. The test client now reads the
  code from the close frame, as the phone app does: 300 runs, 0 failures.
- The new lockout recorded a failed pairing only after answering, so a quick retry could slip in uncounted.
  It now records the failure first.

### Decided (review welcome)
- The untrusted-content rule has two levels instead of making every change ask. With one level, a coding task
  asked before every edit after its first file read, which would push people to turn the rule off entirely.
- "Delete" in the never-always-allow list means deleting files. Forgetting a memory still asks, but a spoken
  yes is enough.
- Basics can't be switched off: planning tools and memory live there. Single tools can still be denied.

### Not done yet
- CI with pip-audit, Dependabot and CodeQL comes with the repository work (Phase 5).
- The approval dialog's click-only wording was checked by tests, not on screen: showing it needs a live
  approval.

---

## 2026-10-23 · Phase 3, part 2: error banners, uninstall and updates, the user guide, help

### What changed

**Errors with a fix button** (`src/app/errors.py`)
- The error catalogue: an error's stage and text become one plain sentence and the button that fixes it:
  microphone blocked by Windows privacy (Open privacy settings), microphone missing, speech recognition not
  downloaded, voice unavailable, Claude Code missing (Get Claude ready), signed out of Claude (Sign in),
  Claude's usage limit with the reset time (Use an API key instead), a rejected API key (Change key),
  offline (Try again), the AI not set up, the daily budget, a failed download (Retry).
- The window shows them as a bar under the top bar (at most two, each dismissible); fix buttons open the
  right Settings section, start the Claude sign-in or install, retry a download or restart the assistant.
  A successful start clears the AI and voice bars.
- With Claude as the brain, the app checks Claude Code at start-up and shows "You're signed out of Claude" or
  "Claude needs Claude Code" before the user says anything.
- Spoken approvals use the name: "Nova wants to delete memory #3. Allow?".

**Claude Code install**: "Install it for me" (wizard) and "Get Claude ready" (banner) run Anthropic's
documented Windows installer (`irm https://claude.ai/install.ps1 | iex`) without a window, then check for the
CLI. Not run on this machine (it would reinstall the author's Claude Code); it waits for the clean-machine
spike, and the official setup page stays one click away.

**Settings › About** (`src/app/uninstall.py`, `src/app/updates.py`, `src/version.py`)
- Version, the data and models folders, whether this is an installed copy or a source checkout.
- Check for updates (and on start-up): reads the website's `latest.json` (`updates.url`, empty until the site
  exists), compares versions (dev < alpha < beta < rc < release), and can download the release zip and check
  its SHA-256. Swapping the app folder needs the packaged updater (Phase 6); until then a new version is
  offered as a download link.
- Uninstall {name}: removes the start-up shortcut, Start Menu and Desktop shortcuts and the Apps-list entry;
  with "also delete my data", the saved keys and passwords and the data, models and cache folders. The
  confirmation lists what will go. Open files are deleted by a throwaway script that waits for Jarvis to exit.
  It never deletes a drive root, the user's home folder or a folder containing the app, and a source checkout
  never deletes itself.

**User guide** (`docs/GETTING_STARTED.md`): install (source for now), the setup wizard screen by screen with
screenshots (demo account and paths, `docs/images/`), what gets downloaded, everyday use, permissions, other
ways to connect, troubleshooting that matches the error bars, update and uninstall, privacy.

**Help, tooltips, keyboard**: a ? button on the wizard and on the Settings sections that have a guide section
opens the guide there (`help.url` once the website exists; the shipped guide until then); every Settings
control shows its help text as a tooltip; Enter moves on in the wizard; focus rings on the option cards.

**Chatterbox** (`scripts/setup_chatterbox.py`): builds a separate environment in the models folder with a
Python 3.10–3.13 (py launcher or `--python`), installs `chatterbox-tts` 0.1.7 (and CUDA torch with
`--cuda`), checks it imports, and saves its settings; `--use` switches the voice; `--dry-run` shows the steps.

### Verified
- 366 automated tests pass (19 new): the catalogue's mapping for every error kind, the spoken approval with a
  name, the installer call, the uninstall plan with and without data, the folder safety rules, the cleanup
  script (waits for the process, then deletes only the allowed folders), version ordering, the update check and
  its checksum, every ? anchor and screenshot in the guide, and the Chatterbox setup steps and settings.
- Live in a scratch data folder: a real start-up error (Ollama chosen, no model) showed "The AI connection
  isn't set up yet" with **Open Settings › AI**, and the button opened Settings; the About section rendered.
- Bugs caught by the tests and fixed before they shipped: the uninstall script lost its wait-for-exit line in
  an edit (it would have deleted folders while Jarvis was still running), and its line endings doubled.
- Personal-data scan over every changed or new file: 0 hits (the screenshots show only demo details).

### Not done yet (rest of Phase 3)
- The Sign in with Claude spike on clean Windows and macOS machines (unattended install, fully hidden login).
- The usability test with three non-technical people.
- Kokoro (waits for the voice and license decision).
- The update swap itself, Install for me and the Apps-list entry come with the packaged app (Phase 6).

---

## 2026-10-23 · Phase 3, part 1: setup wizard, the assistant's name, models and downloads

### What changed

**The assistant's name** (`assistant.name`, default "Jarvis")
- `config_store.check_assistant_name()` (2–20 letters, with spaces, hyphens or apostrophes between them; a
  warning for common words such as "Computer") and `assistant_name()`.
- Used in the system prompt ("You are Nova…"; when renamed, one sentence tells the model that tool texts
  call the app Jarvis), the greeting prefix ("{name} is back.", also Chatterbox's cached phrase), the
  greeting prompt, the name stripped from commands ("Nova, open notepad"; the product name too), Whisper's
  initial prompt, the tray, window titles and notifications (live on rename), the phone server and the
  phone app (`assistant_name` in the status; the app shows it everywhere it used to say Jarvis).
- Web UI: every user-facing text that names the assistant comes from `src/gui/web/strings.js` with a
  `{name}` placeholder (also the start of translations); the orb's wordmark is the name. Settings › General
  holds the name, with live hints, and "Run setup again".
- Technical names (files, folders, logs, credentials, the MCP prefix) stay "Jarvis".

**Models** (`config/models.json`, `src/core/models.py`)
- The catalogue: speech detection (Silero VAD), speech recognition (Whisper base, small, medium, large-v3
  turbo) and voices (the Windows voice, four Piper voices with hosted samples). Each option has a plain
  description, maker, license and its link, official page, file URLs pinned to a commit, sizes and SHA-256
  sums (taken from the hubs' metadata, or by hashing the small files), and the settings choosing it writes.
- Recommendations come from ordered rules in the catalogue, evaluated by a small parser (never `eval`).
  The speech recognition rules follow the hardware table, with one addition: CUDA is only recommended when
  cuBLAS and cuDNN for CUDA 12 can be found, so a GPU without them gets the CPU instead of a failing load.
- Correction to the plan: the large-v3 turbo repository it named doesn't exist; the catalogue uses
  `deepdml/faster-whisper-large-v3-turbo-ct2`.

**Hardware detection** (`src/core/hardware.py`): logical cores, RAM, NVIDIA GPU name and total/free memory
(nvidia-smi), and whether the CUDA libraries are present.

**Downloads** (`src/core/downloads.py`): one at a time on a worker thread; resumable with HTTP Range after an
interruption or a restart; retried on network errors; every file checked against its SHA-256 before it is
used (a mismatch deletes it); "I already have this file" imports a file after checking it; progress
events with speed and time left.

**Setup wizard** (`src/app/setup.py`, `src/gui/web/setup.js`)
- Shown while `setup.complete` is false (a new install, or "Run setup again"); existing settings files are
  migrated as already set up (config version 3). The assistant doesn't start until the last screen.
- Preloader (the HUD orb fades in while hardware detection really runs, at least 1.5 s, fade only with
  reduced motion) → Name your AI (ideas, live hints, "Hear it" with the Windows voice) → What {name} needs
  (the catalogue table with the pick for this computer and why, Change, ⓘ details with license and official
  page, Download manually, I already have this file, total size and folder) → Connect (Sign in with Claude,
  or other ways to connect: provider, key, model, Fetch models, Check) → Microphone (device, live level, a
  5-second test that transcribes when the speech model is there, a fix button when Windows privacy blocks the
  microphone, a way on without one) → Voice (samples, "don't speak") → About you (your name, folders it may
  work in; written to the profile) → What {name} may do (Everyday, Work, Full control, mapped to tool
  permissions) → Start-up → Try it (the real assistant; typed or spoken). Back on every screen, progress
  dots, and it resumes after the last saved screen.
- A download bar shows the current download and offers Retry when one fails.

**Sign in with Claude**: Jarvis finds the Claude Code CLI (settings, PATH, or the official installer's
folder), reads `claude auth status --json` (signed in, account, plan; Jarvis never touches the
credentials) and starts `claude auth login --claudeai` without a window, checking every 2 s for up to 5
minutes. Installing Claude Code automatically waits for the spike on a clean machine; until then the
screen links to the official setup page.

**Voices and speech detection**
- The Windows voice (`src/tts/system.py`, SAPI into memory, played like the other engines): `tts.engine:
  system`, a voice and speed in Settings, and the fallback when the chosen voice can't start
  (`tts.fallback`), so a fresh install can always speak.
- Speech detection runs Silero VAD through ONNX Runtime (`src/stt/vad.py`), mirroring Silero's own wrapper.
  torch and the silero-vad package are no longer needed by the core install (Chatterbox keeps its own
  runtime).

**Also**
- Settings › Models: in use, recommended, downloaded or downloading; Use this, Download, Stop, Delete, and
  each file's manual download or import.
- Common apps found in the Start Menu (`src/core/app_discovery.py`, a curated list so the open_app tool
  stays short; `apps.discover`); a shortcut opens like a double-click.
- `python jarvis_gui.pyw --diagnose` and Settings › Save a report: a redacted report (system, packages,
  hardware, audio devices, settings without keys, models on disk, Claude Code version, recent log) with the
  user folder and email addresses masked, saved in the data folder.

### Verified
- 347 automated tests pass (47 new): the name rules and a static check that no web UI file outside
  `strings.js` names the assistant, a renamed assistant in prompt, greetings, command stripping and speech
  vocabulary; the catalogue's integrity (pinned URLs, checksums, sizes, every setting it writes is valid);
  the hardware table; the downloader against a local server (resume with a Range request, checksum
  mismatch, missing file, import, delete); the wizard's backend for every screen; the Claude status parsing;
  the Windows voice and its fallback; speech detection on silence and on real synthesized speech without
  importing torch; Start Menu discovery; the diagnostic report's masking.
- Phone app: `npx tsc --noEmit` clean, 16 Jest tests pass.
- Live on this machine: hardware detection (20 cores, a 4 GB RTX 3050 without cuDNN → Whisper small on the
  CPU), `claude auth status --json` (signed in), the Windows voice, 13 common apps found in 22 ms.
- GUI smoke runs in scratch data folders: the wizard's screens render and react to a rename; Settings ›
  General and Settings › Models render. Two bugs found that way and fixed: texts kept the old name after a
  rename, and the voice cards' play button wrapped onto its own row.
- Personal-data scan over every changed or new file: 0 hits.

### Decisions taken (review welcome)
- The voice recommended by the catalogue stays Piper Amy (today's default) until the voice decision. Kokoro
  is left out for now: `kokoro-onnx` depends on `phonemizer` (GPL-3.0-or-later) and `espeakng-loader`
  (espeak-ng, GPL-3.0), the same licensing question as Piper.
- The NVIDIA helper libraries are not in the catalogue yet (they are pip wheels); CUDA is recommended only
  where they are already installed.

### Not done yet (rest of Phase 3)
- The Sign in with Claude spike on clean Windows and macOS machines (unattended install, fully hidden
  login).
- Error catalogue with fix buttons in the main window, signed-out and usage-limit banners; the name in
  spoken approvals.
- Auto-update, uninstall with "delete my data", Install for me (these come with the packaged app).
- In-app help links, tooltips, keyboard navigation review; the end-user guide with screenshots; the
  usability test with three non-technical people.
- Kokoro (waits for the voice and license decision) and `scripts/setup_chatterbox.py`.

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
