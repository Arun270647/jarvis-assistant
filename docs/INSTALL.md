# Installing Jarvis

Jarvis comes as a zip for Windows with its own Python inside, so you don't need to install anything else. The
first release isn't published yet: until then, run it from source (below) or build the zip yourself
([CONTRIBUTING.md](../CONTRIBUTING.md#building-the-windows-zip)). Once it's running, the setup wizard takes over
([GETTING_STARTED.md](GETTING_STARTED.md)).

## What you need

- Windows 11 (Windows 10 may work but isn't tested).
- Python 3.11, 3.12 or 3.13 from [python.org](https://www.python.org/downloads/windows/) (3.12 recommended).
- Git, or download the repository as a zip.
- A microphone if you want to talk to it (typing works without one).
- About 2 GB of disk space with the default speech model; an NVIDIA graphics card makes speech recognition
  faster but isn't needed.
- One way to reach an AI model: a Claude account (the default, through Claude Code), an API key for another
  provider, or a local model in Ollama or LM Studio. See [PROVIDERS.md](PROVIDERS.md).

## Install from the zip

1. Download `Jarvis-<version>-windows-x64.zip` (about 300 MB).
2. Right-click it, choose **Extract All**, then **Extract**. Jarvis can't start from inside the zip; if you try,
   it says so.
3. Open the extracted **Jarvis** folder and double-click **Jarvis.exe**.
4. Windows may say "Windows protected your PC". Jarvis isn't code-signed (it's free and has no budget for a
   certificate), so SmartScreen warns the first time. Click **More info**, then **Run anyway**.
5. At the end of the setup wizard, **Install for me** copies Jarvis to `%LOCALAPPDATA%\Programs\Jarvis`, adds it
   to the Start Menu and to Settings › Apps, and deletes the extracted copy. No administrator rights are needed.
   **Keep it here** leaves it where it is (a portable copy); Settings › About offers Install for me later too.

**Check the download.** The release page lists the zip's SHA-256. In PowerShell, in your Downloads folder, run
`Get-FileHash Jarvis-<version>-windows-x64.zip` and compare the result with it. If they differ, delete the file
and download it again.

The zip contains `Jarvis.exe` (a small launcher), `Jarvis-Updater.exe`, `runtime` (Python 3.13 with the packages
Jarvis uses) and `app` (Jarvis itself). Your settings, memories and models are not in that folder, so updating
or moving it keeps them.

## Install from source

In PowerShell or a Command Prompt:

```
git clone <this repository> jarvis-assistant
cd jarvis-assistant
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw jarvis_gui.pyw
```

The window opens with the setup wizard. It downloads the speech model and the voice you pick, and connects the
AI.

`requirements.txt` takes the newest compatible versions. For the exact versions that CI tests, install
`requirements.lock` instead:

```
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
```

## Voices

- **Kokoro** is the default: natural voices that run on the processor. Its engine uses espeak-ng and
  phonemizer, which are GPL-3.0, so Jarvis doesn't include it. Picking a Kokoro voice in the wizard or in
  Settings › Models downloads the voice model and installs the engine from PyPI into the models folder. For a
  source install you can also put it in your environment: `.venv\Scripts\python -m pip install -e .[kokoro]`.
- **Piper voices** are quicker still and sound a little flatter. The Piper engine is GPL-3.0 too, and installs
  the same way. From the command line: `.venv\Scripts\python scripts\setup_piper.py`.
- **Chatterbox** sounds the most natural, but needs PyTorch (several GB) and is slow without an NVIDIA graphics
  card. It gets its own Python environment: `.venv\Scripts\python scripts\setup_chatterbox.py --cuda --use`
  (leave out `--cuda` without an NVIDIA card).
- **The Windows voice** is built in and needs nothing.

## Where your files go

| What | Where |
|---|---|
| Settings (`config.json`, only what you changed), profile, database, logs | `%APPDATA%\Jarvis` |
| Downloaded models, add-ons and caches | `%LOCALAPPDATA%\Jarvis` |
| API keys and the email password | Windows Credential Manager |

Set the environment variable `JARVIS_HOME` to a folder to keep all of it (except the keys) in that folder:
useful for a portable copy or for development. The app folder itself is never written to.

## Start with Windows

The setup wizard asks, and Settings › Windows and tray changes it later. Jarvis adds a shortcut to your
Startup folder, and nothing else.

## Updating

The zip updates itself. Once the download site is live, Jarvis checks for a new version when it starts
(Settings › About turns this off), downloads it in the background, checks its SHA-256 and shows
**Restart to update**. Jarvis then closes, `Jarvis-Updater.exe` puts the new version in place and starts it
again. If something goes wrong, the updater puts the old version back and Jarvis says why.

### Updating a source install

```
git pull
.venv\Scripts\python -m pip install -r requirements.txt
```

Your settings carry over: Jarvis upgrades its settings file by itself when the format changes.

## Uninstalling

Settings › About › **Uninstall Jarvis** removes the shortcuts, the entry in Settings › Apps and, if you tick it,
your data, models and saved keys. The zip's Jarvis folder is deleted once Jarvis has closed. Settings › Apps ›
Jarvis › **Uninstall** opens the same page. A source install doesn't delete its own folder: delete the
`jarvis-assistant` folder yourself afterwards.
