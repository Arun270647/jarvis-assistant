# Installing Jarvis

There is no packaged download yet: it comes with the first release, as a signed zip with its own Python, so
you won't need to install anything else. Until then, Jarvis runs from source. Once it's running, the setup
wizard takes over ([GETTING_STARTED.md](GETTING_STARTED.md)).

## What you need

- Windows 11 (Windows 10 may work but isn't tested).
- Python 3.11, 3.12 or 3.13 from [python.org](https://www.python.org/downloads/windows/) (3.12 recommended).
- Git, or download the repository as a zip.
- A microphone if you want to talk to it (typing works without one).
- About 2 GB of disk space with the default speech model; an NVIDIA graphics card makes speech recognition
  faster but isn't needed.
- One way to reach an AI model: a Claude account (the default, through Claude Code), an API key for another
  provider, or a local model in Ollama or LM Studio. See [PROVIDERS.md](PROVIDERS.md).

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

## Optional voices

- **Piper voices** sound natural and start instantly. The Piper engine is GPL-3.0, so Jarvis doesn't include
  it. Picking a Piper voice in the wizard or in Settings › Models installs the engine from PyPI into the models
  folder, together with the voice. From the command line: `.venv\Scripts\python scripts\setup_piper.py`.
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

## Updating a source install

```
git pull
.venv\Scripts\python -m pip install -r requirements.txt
```

Your settings carry over: Jarvis upgrades its settings file by itself when the format changes.

## Uninstalling

Settings › About › **Uninstall Jarvis** removes the shortcuts and, if you tick it, your data, models and saved
keys. A source install doesn't delete its own folder: delete the `jarvis-assistant` folder yourself afterwards.
