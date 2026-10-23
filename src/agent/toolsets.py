"""Which tools the model is shown (tools.profile). Small local models cope with a short list, not all 45.

    full    every registered tool (Claude, GPT-class and Gemini models)
    core    apps, media, clock, memory, web reading, read-only project files and tasks
    custom  the names in tools.custom_list
    auto    core for providers marked local (Ollama, LM Studio), full otherwise; a provider entry may set
            its own "tool_profile"

A profile only decides what the model sees. Permissions, approvals and validation stay in PolicyExecutor,
and a backend refuses a call to a tool it did not offer.
"""
PROFILES = ("auto", "full", "core", "custom")

CORE_TOOLS = (
    "open_app", "media_control", "get_datetime",
    "remember", "recall", "forget",
    "search_web", "read_webpage",
    "project_context", "list_dir", "read_file", "search_files",
    "create_plan", "update_step", "report_progress", "finish_task", "block_task", "resume_task",
    "note_capability_gap",
)


def profile_for(config: dict, provider_settings: dict) -> str:
    profile = (config.get("tools", {}) or {}).get("profile", "auto")
    if profile not in PROFILES:
        profile = "auto"
    if profile == "auto":
        profile = provider_settings.get("tool_profile") or ("core" if provider_settings.get("local") else "full")
    return profile


def tool_names(registry, config: dict, provider_settings: dict) -> list:
    """Registered tool names the model may see, in registry order."""
    names = registry.names()
    profile = profile_for(config, provider_settings)
    if profile == "core":
        allowed = set(CORE_TOOLS)
    elif profile == "custom":
        allowed = set((config.get("tools", {}) or {}).get("custom_list") or [])
    else:
        return names
    return [n for n in names if n in allowed]
