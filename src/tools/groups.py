"""Tool groups: what the assistant may do at all.

Settings › Tools and permissions switches groups on and off (tools.groups), and the setup wizard's
"What {name} may do" choice picks a set of them. A tool whose group is off is not registered, so the model
never sees it; permissions (allow / ask / deny) and approvals apply to the tools that remain.

The risky groups are off on a new install. Basics can't be switched off (planning tools and memory live
there; deny single tools instead). Extension tools are in no group: they load only when listed in
tools.extensions.
"""
import sys
GROUPS = {
    "basic": {
        "title": "Basics",
        "text": "Clock, memory, opening apps, media keys, opening Chrome, reading web pages and web search.",
        "default": True, "locked": True,
        "tools": ("get_datetime", "remember", "recall", "forget", "open_app", "media_control", "open_chrome",
                  "list_chrome_profiles", "read_webpage", "search_web", "create_plan", "update_step",
                  "report_progress", "finish_task", "block_task", "resume_task", "note_capability_gap"),
    },
    "workspace_read": {
        "title": "Read your project folders",
        "text": "List, read and search files in the folders you add (git status and log included). Secret files "
                "such as .env and keys are never read.",
        "default": True,
        "tools": ("project_context", "list_dir", "read_file", "search_files"),
    },
    "workspace_write": {
        "title": "Change your project folders",
        "text": "Edit, write and delete files in those folders (backups kept), run allowed project commands and "
                "background processes.",
        "default": False,
        "tools": ("edit_file", "write_file", "delete_file", "run_command", "process_output", "stop_process",
                  "check_process", "http_check"),
    },
    "terminals": {
        "title": "Command Prompts",
        "text": "Open Command Prompt windows and type into them, for example to start a coding agent you can "
                "watch.",
        "default": False,
        "tools": ("open_terminal", "terminal_type", "terminal_key", "terminal_read"),
    },
    "desktop": {
        "title": "Control other apps",
        "text": "Look at windows, take screenshots, click, type and scroll in other apps like a person would. "
                "Consoles, password managers and admin prompts stay off limits.",
        "default": False,
        "tools": ("desktop_windows", "desktop_snapshot", "desktop_screenshot", "desktop_click", "desktop_type",
                  "desktop_keys", "desktop_scroll", "desktop_focus"),
    },
    "system": {
        "title": "Lock and power",
        "text": "Lock the PC right away; sleep, shut down and restart always ask first.",
        "default": True,
        "tools": ("system_command",),
    },
    "email": {
        "title": "Email",
        "text": "Send email from your account (always asks unless every recipient is on your trusted list) and "
                "open drafts in your mail app.",
        "default": False,
        "tools": ("send_email", "draft_email"),
    },
    "coding_agent": {
        "title": "Hidden coding agent runs",
        "text": "Run a coding agent (Claude Code, Codex, Aider) without a window in one of your projects. Asks "
                "first.",
        "default": False,
        "tools": ("delegate_to_coding_agent",),
    },
}

WINDOWS_TITLES = {gid: g["title"] for gid, g in GROUPS.items()}     # docs/TOOLS.md's section names
# On a Mac the same groups use the Mac's words.
MAC_WORDING = {
    "terminals": {"title": "Terminal windows",
                  "text": "Open Terminal windows and type into them, for example to start a coding agent you can "
                          "watch."},
    "system": {"text": "Lock the Mac right away; sleep, shut down and restart always ask first."},
}
if sys.platform == "darwin":
    for _gid, _words in MAC_WORDING.items():
        GROUPS[_gid] = dict(GROUPS[_gid], **_words)

GROUP_OF = {tool: gid for gid, g in GROUPS.items() for tool in g["tools"]}

# What Settings says about the tools that always ask in some or all uses (ToolSpec.always_ask).
ALWAYS_ASK_NOTES = {
    "delete_file": "Always asks, and a spoken yes isn't enough.",
    "send_email": "Mail to anyone outside your trusted list always asks, with a click or key.",
    "system_command": "Sleep asks; shutting down and restarting always ask, with a click or key.",
    "terminal_type": "Typing into a running program always asks, with a click or key.",
}

# The untrusted-content rule (policy.py). Untrusted output from these groups is the user's own project: its
# files, command output and the Command Prompts opened in its folders. After it, changes inside the project
# don't ask again (they are confined and backed up, and whoever wrote those files can already run code
# through the project's own commands), but anything else that changes something does. Untrusted output from
# anywhere else (web pages, screens, http_check's URLs, extensions) makes every change ask.
PROJECT_GROUPS = ("workspace_read", "workspace_write", "terminals")
OUTSIDE_SOURCES = ("http_check",)

# The wizard's choices, from least to most (src/app/setup.py shows them).
PRESETS = {
    "everyday": ("basic", "workspace_read", "system"),
    "work": ("basic", "workspace_read", "system", "workspace_write", "terminals", "email", "coding_agent"),
    "full": tuple(GROUPS),
}


def group_of(tool: str):
    """The tool's group id, or None (extension tools)."""
    return GROUP_OF.get(tool)


def enabled_groups(config: dict) -> set:
    """Group ids that are on: each group's default, overridden by tools.groups. Basics are always on."""
    chosen = (config.get("tools", {}) or {}).get("groups", {}) or {}
    on = {gid for gid, g in GROUPS.items() if bool(chosen.get(gid, g["default"]))}
    return on | {gid for gid, g in GROUPS.items() if g.get("locked")}


def tool_enabled(tool: str, config: dict) -> bool:
    gid = group_of(tool)
    return gid is None or gid in enabled_groups(config)


def taint_level(tool: str) -> str:
    """How far untrusted output from this tool is trusted: "project" or "outside"."""
    return "project" if GROUP_OF.get(tool) in PROJECT_GROUPS and tool not in OUTSIDE_SOURCES else "outside"


def project_tool(tool: str) -> bool:
    return GROUP_OF.get(tool) in ("workspace_write", "terminals")


def groups_setting(preset: str) -> dict:
    """tools.groups for a wizard preset: every switchable group, on or off."""
    on = set(PRESETS[preset])
    return {gid: gid in on for gid, g in GROUPS.items() if not g.get("locked")}


def preset_for(config: dict):
    """The preset whose groups are exactly the ones on now, or None."""
    on = enabled_groups(config)
    return next((p for p, gids in PRESETS.items() if set(gids) == on), None)
