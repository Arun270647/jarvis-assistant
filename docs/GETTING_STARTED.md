# Getting started with Jarvis

Jarvis is an assistant you talk to. Ask it to open apps, play music, look things up, find files and more. It
always asks before doing anything important.

This guide takes about 5 minutes. You don't need any technical knowledge once Jarvis is installed.

**You need:** a Windows 11 PC, a microphone (most laptops have one; without one you can type), an internet
connection, and a **Claude Pro or Max** plan (sign up at claude.ai). No Claude plan? See
[Other ways to connect](#5-other-ways-to-connect).

> Jarvis isn't released yet. Until there is a download, it runs from its source code: see
> [Running from source](../README.md#running-from-source). The steps below start once it opens.

---

## 1. Install

For now, follow [Running from source](../README.md#running-from-source) in the README. With the first
release this becomes: download the zip from the website, extract it, double-click **Jarvis**.

## 2. Set up (the setup wizard)

When Jarvis opens for the first time, a short setup guides you. Just follow the big button on each screen.
**Back** takes you to the previous screen, and if you close Jarvis halfway it continues where you stopped.

1. **Name your AI**: choose a name for your assistant (or keep the suggestion) and press **▶ Hear it**. Your
   assistant answers to this name from now on; below, we call it Jarvis.

   ![Name your AI](images/setup-name.png)

2. **What Jarvis needs**: Jarvis shows the free AI models it will download and what each one does. It has
   already picked the best ones for your computer, so just click **Download and continue**. Click **Change**
   to pick another one, or **ⓘ** to learn more and see the official page and license.

   ![What Jarvis needs](images/setup-models.png)

3. **Connect your AI**: click **Sign in with Claude**. Your web browser opens. Sign in with **Google** or your
   email, then go back to Jarvis; it shows a tick with your account. If Claude Code (Anthropic's app that
   Jarvis uses for this) isn't on your computer yet, click **Install it for me** first.

   ![Connect your AI](images/setup-connect.png)

4. **Microphone**: pick your microphone if it isn't already chosen, click **Test** and say "Hello Jarvis".
   The bar shows your voice level. If Windows blocks the microphone, click **Open privacy settings**.
5. **Voice**: press **▶** to hear the voices and pick the one you like. Tick **Don't speak, just show text** if
   you prefer reading.

   ![Pick a voice](images/setup-voice.png)

6. **About you** (optional): tell Jarvis your name, and add folders it may work in. You can click **Skip**.
7. **What Jarvis may do**: keep **Everyday** if you're unsure. You can change this later.

   ![What Jarvis may do](images/setup-permissions.png)

8. **Start-up**: choose whether Jarvis starts when you sign in to Windows.
9. **Try it**: click **Talk** and say **"What time is it?"**, or type it. Then click **Finish**.

While you do this, Jarvis downloads its models in the background (about 550 MB on most computers); a bar at
the bottom shows the progress. If they're still downloading at the end, you can type to Jarvis until they're
ready.

### What gets downloaded

All of these are free and run on your own computer. You can change them later in **Settings › Models**.

| What | What it does | Default | Size | Official page |
|---|---|---|---|---|
| Speech detection | Notices when you start and stop talking | Silero VAD | 2 MB | https://github.com/snakers4/silero-vad |
| Speech recognition | Turns your voice into text | Whisper *small* (*medium* with an NVIDIA graphics card and its CUDA libraries) | 486 MB (1.5 GB) | https://huggingface.co/Systran/faster-whisper-small |
| Voice | Reads answers aloud | Piper · Amy, or the Windows voice (nothing to download) | 63 MB | https://huggingface.co/rhasspy/piper-voices |

**Slow or no internet at setup?** Click **ⓘ** on a row › **Download manually**: your browser downloads the
file. Then click **I already have this file** and pick it. Jarvis checks the file is the right one before using
it. Downloads that stop halfway continue where they stopped.

## 3. Everyday use

- **Talk:** click the orb, or press **Ctrl+Shift+J**, then speak. Jarvis stops listening when you stop talking.
- **Hold to talk:** in the Jarvis window, hold **Space** while you speak.
- **Type:** write in the box under the orb and press Enter.
- **Mute the microphone:** **Ctrl+M** or the microphone button.
- **Approve or refuse:** when Jarvis wants to do something important, it shows a box and asks out loud. Click
  **Allow** or **Deny**, press **Y** or **N**, or say "yes" / "no".
- **Hide it:** close the window. Jarvis keeps running as a small orb and an icon in the taskbar tray.
  Right-click the icon to quit.

![The Jarvis window](images/main-window.png)

Things to try:
- "Open Notepad" / "Open Chrome"
- "Play the next song" / "Turn the volume down"
- "What's the weather in Chennai?"
- "Remember that my passport is in the blue drawer" … later: "Where is my passport?"
- "Summarize this web page: <link>"

## 4. Change what Jarvis is allowed to do

Open **Settings › Tools and permissions**, or run the setup again (**Settings › General › Run setup again**)
and pick another card:

- **Everyday**: apps, music and volume, time, web, memory, reading the folders you added, locking the PC.
- **Work**: also edits files in those folders, runs project commands, opens Command Prompts, sends email and
  runs coding agents. Asks before risky changes.
- **Full control**: also looks at, clicks and types in other apps for you and takes screenshots.

In **Settings › Tools and permissions** you can also switch each group on or off by itself.

Jarvis **always** asks before deleting a file, emailing someone who isn't on your trusted list, shutting down
or restarting your computer, or typing into a running program. For these, click **Allow** or press **Y**: a
spoken "yes" isn't enough. After it reads a web page or a screen, it also asks before any change. The details
are in [What Jarvis can do on your PC, and how to limit it](SECURITY_MODEL.md).

## 5. Other ways to connect

In the setup (or **Settings › AI**), click **Other ways to connect**:

- **Claude API key**: pay per use instead of a plan. Create a key in the Anthropic Console and paste it.
- **Free and private**: runs an AI model on your own computer with Ollama or LM Studio. Needs a powerful
  computer (16 GB of memory or more).
- **Other providers**: OpenAI, Google Gemini, OpenRouter, Groq and others with an API key.

Details: [AI providers](PROVIDERS.md).

![Settings › AI](images/settings-ai.png)

## 6. Something's not working?

When something goes wrong, Jarvis shows a bar at the top of its window that says what happened, with a button
that fixes it.

| Problem | What to do |
|---|---|
| Jarvis doesn't hear me | **Settings › Microphone**: check the microphone. If the bar says access is blocked, click **Open privacy settings** and turn microphone access on |
| "Speech recognition isn't downloaded yet" | Click **Open Models** and download it, or wait for the download to finish |
| "You're signed out of Claude" | Click **Sign in** and sign in again in the browser |
| "Claude's usage limit is reached" | Your plan's limit resets at the time shown. You can add an API key meanwhile (**Use an API key instead**) |
| "This API key didn't work" | Click **Change key** and paste a new one |
| Voice sounds robotic or slow | **Settings › Models** or **Settings › Voice**: pick another voice |
| Anything else | **Settings › Logging and diagnostics › Save a report**, then attach the file when you report the problem. The report never contains your passwords or keys, and your user folder and email addresses are hidden |

## 7. Update and uninstall

- **Updates:** **Settings › About › Check for updates** (automatic checks start with the first release).
- **Uninstall:** **Settings › About › Uninstall**. Tick the box first if you also want your settings,
  memories, downloaded models and saved keys deleted.

## Privacy in one paragraph

Listening only starts when you click or press the shortcut. Speech is turned into text on your computer. Only the
text of your request (and, with Full control, screenshots you allow) is sent to the AI you connected. Your history
and memories stay on your computer. Jarvis has no ads, no tracking and no telemetry.
