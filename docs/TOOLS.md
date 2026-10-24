# Tools

Tools are the only things the AI can do on your PC. Each one has a strict description of its arguments, a risk
level and a default permission, and every call goes through the same checks (see
[SECURITY_MODEL.md](SECURITY_MODEL.md#how-every-action-is-checked)). Tools are grouped. A group that is off isn't
shown to the AI at all. Switch groups and change permissions in **Settings › Tools and permissions**.

- **Risk**: `safe` only reads or does something harmless; `sensitive` changes something or reaches outside;
  `destructive` deletes something.
- **Default**: `allow` runs without asking; `ask` shows the approval dialog first. You can change a tool to
  allow, ask or deny, except that tools which always ask can't be set to allow.
- **Asks when** lists the cases that ask even when the tool is set to allow. On top of these, after the AI reads
  untrusted content in a request, every `sensitive` or `destructive` tool that changes something asks (see
  [SECURITY_MODEL.md](SECURITY_MODEL.md#instructions-hidden-in-what-jarvis-reads)).

## Basics

Always on.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `get_datetime` | Reads the clock | safe | allow | |
| `remember` | Stores a fact, preference, note or project convention for later conversations; refuses anything that looks like a password or key | safe | allow | it's a preference or convention (`memory.approval_required_kinds`) |
| `recall` | Searches stored memories | safe | allow | |
| `forget` | Deletes one memory | destructive | ask | |
| `open_app` | Opens an app from your profile or the Start Menu | safe | allow | |
| `media_control` | Sends a media key (play/pause, next, volume) | safe | allow | |
| `open_chrome` | Opens your Chrome in a profile, at a page or a search | safe | allow | |
| `list_chrome_profiles` | Lists your Chrome profiles | safe | allow | |
| `read_webpage` | Reads a public web page in the background, without your logins | safe | allow | |
| `search_web` | Searches the web in the background | safe | allow | |
| `create_plan` | Starts a tracked task with steps and success checks | safe | allow | |
| `update_step` | Marks a task step | safe | allow | |
| `report_progress` | Reports progress on a task | safe | allow | |
| `finish_task` | Finishes a task (its success checks are verified independently) | safe | allow | |
| `block_task` | Stops a task that needs your answer | safe | allow | |
| `resume_task` | Continues a task with your answer | safe | allow | |
| `note_capability_gap` | Records something the assistant couldn't do, as an idea for an extension | safe | allow | |

## Read your project folders

On by default. Works only in the folders you added.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `project_context` | Summarises a project: folder, top-level files, git status, README, allowed commands, earlier task results | safe | allow | |
| `list_dir` | Lists files in a project folder | safe | allow | |
| `read_file` | Reads a text file (never secret files such as `.env` or keys) | safe | allow | |
| `search_files` | Searches file contents with a regular expression | safe | allow | |

## Change your project folders

Off by default. Changes are only allowed inside a tracked task, and every edit keeps a backup.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `edit_file` | Replaces exact text in a file | sensitive | allow | |
| `write_file` | Creates a file, or overwrites one | sensitive | allow | the file already exists |
| `delete_file` | Deletes a file (a recoverable copy is kept) | destructive | ask | always, with a click or key |
| `run_command` | Runs a program in a project folder (never through a shell) | sensitive | allow | the command isn't on an allow-list |
| `process_output` | Reads the output of a background process it started | safe | allow | |
| `stop_process` | Stops a background process it started | safe | allow | |
| `check_process` | Checks whether a process is running | safe | allow | |
| `http_check` | Requests a URL, for example to check a local dev server | safe | allow | |

## Command Prompts

Off by default. Command Prompt windows it opens, in your project folders.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `open_terminal` | Opens a Command Prompt window you can watch | safe | allow | |
| `terminal_type` | Types a line into that window | sensitive | allow | the command isn't allowed or the folder is unknown; typing into a running program always asks, with a click or key |
| `terminal_key` | Presses a key in that window | sensitive | allow | any key but Ctrl+C |
| `terminal_read` | Reads the window's last lines | safe | allow | |

## Control other apps

Off by default. Refused in its own windows, consoles, admin prompts, password managers and password pages.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `desktop_windows` | Lists open windows | safe | allow | |
| `desktop_snapshot` | Reads a window's buttons, fields and text | sensitive | allow | |
| `desktop_screenshot` | Takes a screenshot of a window for the AI to look at | sensitive | allow | |
| `desktop_click` | Clicks in a window | sensitive | allow | |
| `desktop_type` | Types in a window | sensitive | allow | |
| `desktop_keys` | Presses keys in a window (never Windows-key shortcuts) | sensitive | allow | |
| `desktop_scroll` | Scrolls a window | sensitive | allow | |
| `desktop_focus` | Brings a window to the front | sensitive | allow | |

## Lock and power

On by default.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `system_command` | Runs a system command from your profile: lock, sleep, shut down, restart, cancel a shutdown, screenshot tool | sensitive | allow | sleep, or anything marked `"confirm": true` in your profile; shutting down and restarting always ask, with a click or key |

## Email

Off by default. Sending needs your mail settings and an app password (**Settings › Email**).

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `send_email` | Sends email from your account | sensitive | allow | any recipient isn't on your trusted list: always, with a click or key |
| `draft_email` | Opens a pre-filled draft in your mail app for you to send | safe | allow | |

## Hidden coding agent runs

Off by default.

| Tool | What it does | Risk | Default | Asks when |
|---|---|---|---|---|
| `delegate_to_coding_agent` | Runs Claude Code, Codex or Aider without a window in one of your projects and waits for it | sensitive | ask | |

`delegate_to_coding_agent` asks by default; **Ask before delegating to the coding agent** in Settings can turn
that off.

## Extensions

Tools from the `tools_ext` folder that you tick in **Settings › Extensions**. They have no group, and any that
isn't `safe` asks by default. See [EXTENSIONS.md](EXTENSIONS.md).
