# Troubleshooting

When something goes wrong, Jarvis shows a bar at the top of its window with what happened and a button that
fixes it. This page lists those messages and a few problems that don't show a bar. If nothing here helps, see
"Reporting a problem" at the end.

## Messages in the bar

| Message | What it means | What to do |
|---|---|---|
| "… can't use the microphone because it's turned off in privacy settings" | Windows blocks microphone access for apps | **Open privacy settings**, turn on microphone access (also for desktop apps), then restart the assistant |
| "Your microphone was disconnected or isn't available" | The chosen microphone is gone | **Choose another microphone** in Settings › Microphone, or plug it back in. Typing works meanwhile |
| "Speech recognition isn't downloaded yet" | The Whisper model isn't on disk | **Open Models** and download it, or wait for the download to finish |
| "Voice input isn't working right now" | The voice pipeline couldn't start | **Open Settings** › Microphone, then the log (Settings › Logging and diagnostics) shows the reason |
| "…'s voice couldn't start, so answers are shown as text" | The chosen voice can't load: not downloaded, or its engine isn't installed | **Open Models** and click **Download** on the voice. For Kokoro and Piper voices this also installs the engine. Or pick the Windows voice |
| "Claude needs Claude Code, Anthropic's app, which isn't installed" | Sign in with Claude uses Claude Code | **Get Claude ready** installs it; or install it from Anthropic's setup page |
| "You're signed out of Claude" | Claude Code's login expired | **Sign in** and finish in the browser |
| "Claude's usage limit is reached" | Your Claude plan's limit for now | Wait until the time shown, or **Use an API key instead** |
| "This API key didn't work" | The key is mistyped, revoked, or for another provider | **Change key** in Settings › AI and **Test connection** |
| "You're offline, or the AI service can't be reached" | No internet, or the provider is down (for Ollama / LM Studio: the program isn't running) | Check the connection, start Ollama or LM Studio, then **Try again** |
| "The AI connection isn't set up yet" | No model, key or server address chosen | **Open Settings › AI** and finish the connection |
| "Today's AI spending limit is reached" | Your daily limit for API providers | Wait until tomorrow, or **Change the limit** |
| "A model didn't finish downloading" | The download stopped (connection, disk space) | **Retry**. Downloads resume where they stopped |

## Problems without a message

**It hears me, but nothing happens.** Look at the Activity view. If a tool was **denied**, its group may be off
(Settings › Tools and permissions › What it may do) or the tool is set to deny. If it **timed out**, nobody
answered the approval in time.

**It asks before everything after it read a web page.** That is the untrusted-content rule: after a web page or
a screen, every action that changes something asks first in that request. See
[SECURITY_MODEL.md](SECURITY_MODEL.md#instructions-hidden-in-what-jarvis-reads). You can turn it off in Settings
› Tools and permissions, but leaving it on is safer.

**I said "yes" but it still waits.** Deleting a file, emailing someone outside your trusted list, shutting
down or restarting, and typing into a running program need a click on **Allow** or the **Y** key. A spoken yes
isn't accepted for those. A spoken "no" always works.

**"Desktop control is not allowed in …".** Jarvis refuses to click or type in its own windows, consoles, admin
prompts, password managers and browser password pages. That is on purpose.

**It answers slowly.** Speech recognition on the CPU takes a moment, and a bigger Whisper model is slower:
Settings › Models shows what suits your computer. Answers depend on the AI provider. Small local models are
slower and less reliable with tools; Settings › AI has the options. The Chatterbox voice is slow without an
NVIDIA graphics card: about 10 seconds before each answer starts.

**The voice sounds robotic.** That's the built-in Windows voice, which speaks until the Kokoro voice has
downloaded. Check Settings › Models: the Kokoro row should say it's installed.

**A Kokoro or Piper voice doesn't install.** The voice's engine comes from PyPI, so the PC needs internet
access while it installs. The error in Settings › Models gives pip's reason. From a source install you can also
run `python -m pip install -e .[kokoro]`, or `python scripts/setup_piper.py --engine` for Piper.

**Kokoro starts each answer late.** It runs on the processor, and a slow or busy processor takes longer. The
first sentence plays as soon as it's ready while the next one is made. Settings › Voice › Kokoro speed doesn't
change this; the Piper or Windows voices start sooner.

## Reporting a problem

1. **Settings › Logging and diagnostics › Save a report** (from a source install: `python jarvis_gui.pyw
   --diagnose`). The report masks passwords and keys and hides your user folder and email addresses, but read
   it before you share it.
2. Open a bug report on GitHub with what you said or typed, what happened and what you expected, and attach the
   report.

Security problems go through private reporting instead: see [SECURITY.md](../SECURITY.md).
