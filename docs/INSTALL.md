# Installing Jarvis

Jarvis comes as an installer for Windows (.exe) and as a disk image for Macs with Apple silicon (.dmg), each with
its own Python inside, so you don't need to install anything else. Download them from the website
(https://jarvis-assistant-oss.vercel.app/download) or the
[release page](https://github.com/Arun270647/jarvis-assistant/releases), run it from source (below), or build it
yourself ([CONTRIBUTING.md](../CONTRIBUTING.md#building-the-windows-installer)). Once it's running, the setup
wizard takes over ([GETTING_STARTED.md](GETTING_STARTED.md)).

## What you need

- Windows 11 (Windows 10 may work but isn't tested), or a Mac with Apple silicon (M1 or later) and macOS 14
  Sonoma or later (numpy and ONNX Runtime, which Jarvis needs, require it). Macs with an Intel processor aren't
  supported: ONNX Runtime no longer ships for them.
- For a source install: Python 3.11, 3.12 or 3.13 from [python.org](https://www.python.org/downloads/) (3.12
  recommended), and Git, or download the repository as a zip.
- A microphone if you want to talk to it (typing works without one).
- About 2 GB of disk space with the default speech model; an NVIDIA graphics card makes speech recognition
  faster on Windows but isn't needed.
- One way to reach an AI model: a Claude account (the default, through Claude Code), an API key for another
  provider, or a local model in Ollama or LM Studio. See [PROVIDERS.md](PROVIDERS.md).

## Install on Windows

1. Download `Jarvis-Setup-<version>-windows-x64.exe` (about 150 MB) and open it.
2. Windows may say "Windows protected your PC". Jarvis isn't code-signed (it's free and has no budget for a
   certificate), so SmartScreen warns the first time. Click **More info**, then **Run anyway**.
3. Click **Install**. Jarvis installs for you only, in `%LOCALAPPDATA%\Programs\Jarvis`, without administrator
   rights. It adds itself to the Start Menu (and the desktop, if you tick that) and to Settings › Apps, then opens.

**Check the download.** The release page lists the installer's SHA-256. In PowerShell, in your Downloads folder,
run `Get-FileHash Jarvis-Setup-<version>-windows-x64.exe` and compare the result with it. If they differ, delete
the file and download it again.

The Jarvis folder holds `Jarvis.exe` (a small launcher), `runtime` (Python 3.13 with the packages Jarvis uses) and
`app` (Jarvis itself). Your settings, memories and models are not in that folder, so an update or a reinstall
keeps them. You can delete the installer once Jarvis is running.

## Install on a Mac

1. Download `Jarvis-<version>-macos-arm64.dmg` and open it: a window shows **Jarvis** and your **Applications**
   folder.
2. Drag **Jarvis** onto **Applications**. (If you open Jarvis straight from the disk image instead, its setup
   wizard offers **Install for me** on its last screen: it copies Jarvis into Applications, ejects the disk image
   and deletes the .dmg. Jarvis can update itself only from Applications.)
3. Open **Jarvis** from Applications. Jarvis isn't notarized by Apple (that needs a paid developer account), so the first
   time macOS says it can't check the app for malicious software. Click **Done**, open **System Settings ›
   Privacy & Security**, scroll down to the message about Jarvis and click **Open Anyway**, then **Open**.
   macOS remembers this; it asks only once. (Since macOS 15, right-clicking the app and choosing Open no longer
   skips this check.)
4. macOS asks before Jarvis may use the microphone. The setup wizard explains it first and has an **Allow**
   button. If you clicked Don't Allow, turn **Jarvis** on in **System Settings › Privacy & Security ›
   Microphone**.

**Check the download.** The release page lists the disk image's SHA-256. In Terminal, in your Downloads folder,
run `shasum -a 256 Jarvis-<version>-macos-arm64.dmg` and compare the result with it. Once Jarvis is in
Applications, eject the disk image and delete the .dmg file.

**What macOS asks for, and when.** Only what you use:

| Permission | Needed for | Asked |
|---|---|---|
| Microphone | Talking to Jarvis | In the setup wizard |
| Accessibility | **Full control**: clicking, typing and reading other apps; media keys (play, pause, next) | When you pick Full control, or the first time it's needed |
| Screen & System Audio Recording | **Full control**: screenshots of other apps' windows. Takes effect after Jarvis is reopened | When you pick Full control |
| Automation (Terminal, System Events) | Terminal windows Jarvis opens for you; shutting down or restarting | The first time Jarvis does it |

Settings › Tools and permissions shows what macOS allows right now, with a button to the right System Settings
page. Jarvis.app is signed "ad hoc" (without a developer certificate), so macOS may ask for these again after an
update.

Inside Jarvis.app: a small launcher, `Contents/Resources/runtime` (Python 3.13 with the packages Jarvis uses) and
`Contents/Resources/app` (Jarvis itself). Your settings, memories and models live elsewhere (below).

## Install from source

On Windows, in PowerShell or a Command Prompt:

```
git clone <this repository> jarvis-assistant
cd jarvis-assistant
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw jarvis_gui.pyw
```

On a Mac, in Terminal:

```
git clone <this repository> jarvis-assistant
cd jarvis-assistant
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python jarvis_gui.pyw
```

Run from source, macOS asks for the permissions on behalf of the app you started it from (Terminal, for
example), not Jarvis.

The window opens with the setup wizard. It downloads the speech model and the voice you pick, and connects the
AI.

`requirements.txt` takes the newest compatible versions. For the exact versions that CI tests, install
`requirements.lock` instead:

```
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
```

## Voices

- **Kokoro**: natural voices that run on the processor, offline. It speaks when no voice is chosen. Its engine uses espeak-ng and
  phonemizer, which are GPL-3.0, so Jarvis doesn't include it. Picking a Kokoro voice in the wizard or in
  Settings › Models downloads the voice model and installs the engine from PyPI into the models folder. For a
  source install you can also put it in your environment: `.venv\Scripts\python -m pip install -e .[kokoro]`.
- **Piper voices** are quicker still and sound a little flatter. The Piper engine is GPL-3.0 too, and installs
  the same way. From the command line: `.venv\Scripts\python scripts\setup_piper.py`.
- **Chatterbox** sounds the most natural, but needs PyTorch (several GB) and is slow without an NVIDIA graphics
  card. It gets its own Python environment: `.venv\Scripts\python scripts\setup_chatterbox.py --cuda --use`
  (leave out `--cuda` without an NVIDIA card).
- **Fish Audio** is listed first and recommended in the wizard: the "Jarvis" voice from
  [fish.audio](https://fish.audio/m/29c31f6e158c4c8f84c14f9f3d8ba008/), spoken by Fish Audio's cloud, so it sounds
  exactly as on its page. It needs internet and a Fish Audio API key (fish.audio › API keys), which you paste in
  the wizard or in Settings › Voice; Fish Audio charges that key's account per character (new accounts get free
  credits, and the `s2.1-pro-free` model in Settings › Voice is its free developer tier). Replies are sent to Fish
  Audio to be voiced. Any other fish.audio voice works too: put its id (the code in its address) in Settings ›
  Voice. Without a key or internet, Kokoro (or the system voice) speaks instead.
  - **On your PC instead**: fish-speech, Fish Audio's open-source engine, can run locally and clone a clip you
    give it: `.venv\Scripts\python scripts\setup_fish.py --cuda --hf-token <token> --reference clip.wav
    --reference-text "What the clip says." --use`. It needs PyTorch, a strong NVIDIA card and a Hugging Face
    account (the S1-mini weights, 3.6 GB, are gated: accept their terms first). The open model is smaller than
    Fish Audio's cloud one, so the voice is close rather than identical, and fish-speech and its weights are for
    non-commercial use only. Use a clip of a voice you have the right to copy.
- **The system voice** is built in and needs nothing: the Windows voice, or on a Mac the voices of the `say`
  command (more in System Settings › Accessibility › Spoken Content).

## Where your files go

| What | Windows | macOS |
|---|---|---|
| Settings (`config.json`, only what you changed), profile, database, logs | `%APPDATA%\Jarvis` | `~/Library/Application Support/Jarvis` |
| Downloaded models and add-ons | `%LOCALAPPDATA%\Jarvis` | `~/Library/Application Support/Jarvis/models` |
| Caches | `%LOCALAPPDATA%\Jarvis\cache` | `~/Library/Caches/Jarvis` |
| API keys and the email password | Windows Credential Manager | the Keychain |

Set the environment variable `JARVIS_HOME` to a folder to keep all of it (except the keys) in that folder:
useful for a portable copy or for development. The app folder itself is never written to.

## Start with Windows

The setup wizard asks, and Settings › Windows and tray changes it later. Jarvis adds a shortcut to your
Startup folder, and nothing else. On a Mac it adds a login item for your account (a LaunchAgent; it shows in
System Settings › General › Login Items).

## Updating

Jarvis updates itself. It checks for a new version when it starts (Settings › About turns this off), downloads it
in the background, checks its SHA-256 and shows **Restart to update**. Jarvis then closes, the new version is
installed in its place and Jarvis starts again; on Windows the new installer runs silently, on a Mac the new app
replaces the old one (which comes back if anything fails). Jarvis says what happened. On a Mac this works when
Jarvis is in an Applications folder; a copy that macOS runs straight from Downloads offers the download instead.

### Updating a source install

```
git pull
.venv\Scripts\python -m pip install -r requirements.txt
```

Your settings carry over: Jarvis upgrades its settings file by itself when the format changes.

## Uninstalling

Settings › About › **Uninstall Jarvis** removes the shortcuts, the entry in Settings › Apps and, if you tick it,
your data, models and saved keys. The Jarvis folder is deleted once Jarvis has closed. Settings › Apps ›
Jarvis › **Uninstall** opens the same page. On a Mac the same button removes the login item, Jarvis.app itself
once it has closed, and with the box ticked your data, models and the keys in the Keychain. A source install
doesn't delete its own folder: delete the `jarvis-assistant` folder yourself afterwards.
