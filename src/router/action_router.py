"""Executors behind the open_app, media_control, system_command and delegate_to_coding_agent tools.

Everything is config-driven via the profile (config_store.load_profile()). No hardcoded app paths
or command strings in this module.

Dry-run mode (setting "dry_run": true) logs what would execute without
actually running anything.
"""

import subprocess
import threading
import time
from typing import Optional

from src.core import config_store, paths, winproc

# Coding agents delegate_to_coding_agent can run hidden (print / one-shot mode; the task is appended).
CODING_AGENTS = {
    "claude": ("Claude Code", ["claude", "-p"]),
    "codex": ("Codex", ["codex", "exec"]),
    "aider": ("Aider", ["aider", "--yes-always", "--message"]),
    "custom": ("The coding agent", []),
}


def coding_agent_command(config: dict) -> tuple:
    """(label, command) for coding_agent.cli. Raises ValueError when delegation is off or not set up."""
    ca = config.get("coding_agent", {}) or {}
    cli = ca.get("cli", "claude")
    if cli not in CODING_AGENTS:
        raise ValueError("Coding agent delegation is off")
    label, command = CODING_AGENTS[cli]
    if cli == "custom":
        command = [str(x) for x in (ca.get("custom_command") or [])]
        if not command:
            raise ValueError("No command set for the custom coding agent (coding_agent.custom_command)")
    elif cli == "claude":
        cli_path = ((config.get("llm", {}) or {}).get("providers", {}) or {}).get("claude_code", {}).get("cli_path")
        command = [cli_path or "claude"] + command[1:]
    return label, list(command)


class ActionRouter:
    def __init__(self):
        self.profile = config_store.load_profile()
        self.config = config_store.load_config()
        self.dry_run = self.config.get("dry_run", True)
        self._build_app_index()

    def _build_app_index(self):
        """Build a lookup from alias -> app key for fast matching."""
        self._app_alias = {}
        for key, app in self.profile.get("apps", {}).items():
            self._app_alias[key.lower()] = key
            for alias in app.get("aliases", []):
                self._app_alias[alias.lower()] = key

    def _handle_open_app(self, params: dict) -> dict:
        app_name = (params.get("app_name") or params.get("name") or "").lower()
        app_key = self._app_alias.get(app_name)

        if app_key is None:
            for alias, key in self._app_alias.items():
                if alias in app_name or app_name in alias:
                    app_key = key
                    break

        if app_key is None:
            return self._result("open_app", False, f"App not found in profile: {app_name}")

        app_cfg = self.profile["apps"][app_key]
        app_path = app_cfg["path"]
        args = params.get("args", [])

        if self.dry_run:
            return self._result("open_app", False,
                                f"[DRY RUN] Would open: {app_path} {' '.join(args)}".strip())

        try:
            cmd = [app_path] + (args if isinstance(args, list) else [args])
            winproc.launch_program(cmd, show_console=bool(app_cfg.get("console", False)))
            return self._result("open_app", True, f"Opened: {app_key} ({app_path})")
        except Exception as e:
            return self._result("open_app", False, f"Failed to open {app_key}: {e}")

    def _handle_media_control(self, params: dict) -> dict:
        control = (params.get("control") or params.get("command") or "").lower()
        media_cfg = self.profile.get("media", {})
        mapping = media_cfg.get(control)

        if mapping is None:
            for key in media_cfg:
                if key in control or control in key:
                    mapping = media_cfg[key]
                    control = key
                    break

        if mapping is None:
            return self._result("media_control", False,
                                f"Unknown media control: {control}")

        vk_key = mapping["key"]
        if self.dry_run:
            return self._result("media_control", False,
                                f"[DRY RUN] Would send media key: {vk_key}")

        try:
            _send_media_key(vk_key)
            return self._result("media_control", True, f"Sent media key: {vk_key}")
        except Exception as e:
            return self._result("media_control", False, f"Media control failed: {e}")

    def system_command_needs_confirmation(self, params: dict) -> bool:
        """True when the profile marks this system command with "confirm": true."""
        _, mapping = self._resolve_system_command(params)
        return bool(mapping and mapping.get("confirm", False))

    def _resolve_system_command(self, params: dict):
        cmd_name = (params.get("command") or params.get("name") or "").lower()
        sys_cfg = self.profile.get("system", {})
        if cmd_name in sys_cfg:
            return cmd_name, sys_cfg[cmd_name]
        for key in sys_cfg:
            if cmd_name and (key in cmd_name or cmd_name in key):
                return key, sys_cfg[key]
        return cmd_name, None

    def _handle_system_command(self, params: dict, confirmed: bool = False) -> dict:
        cmd_name = (params.get("command") or params.get("name") or "").lower()
        sys_cfg = self.profile.get("system", {})
        mapping = sys_cfg.get(cmd_name)

        if mapping is None:
            for key in sys_cfg:
                if key in cmd_name or cmd_name in key:
                    mapping = sys_cfg[key]
                    cmd_name = key
                    break

        if mapping is None:
            return self._result("system_command", False,
                                f"Unknown system command: {cmd_name}")

        command = mapping["command"]
        needs_confirm = mapping.get("confirm", False)

        if needs_confirm and not confirmed:
            return self._result("system_command", False,
                                f"[CONFIRM REQUIRED] {cmd_name}: {command}")

        if self.dry_run:
            return self._result("system_command", False,
                                f"[DRY RUN] Would run: {command}")

        try:
            # No shell: a cmd.exe wrapper would open a console window under pythonw.
            winproc.launch_program(command)
            return self._result("system_command", True, f"Executed: {command}")
        except Exception as e:
            return self._result("system_command", False, f"System command failed: {e}")

    def resolve_delegation(self, params: dict) -> dict:
        """Resolve a delegate_to_coding_agent request without running it.

        Returns {"success": True, "task", "project_name", "project_path", "agent"} or
        {"success": False, "error": str}.
        """
        task = (params.get("task") or params.get("prompt") or "").strip()
        if not task:
            return {"success": False, "error": "No task specified for delegation"}
        try:
            label, _ = coding_agent_command(self.config)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        res = _resolve_project((params.get("project") or "").strip(), self.config.get("coding_agent", {}), self.profile)
        if not res["success"]:
            return res
        return {"success": True, "task": task, "project_name": res["project_name"], "project_path": res["path"],
                "agent": label}

    def execute_confirmed_delegation(self, confirmation_data: dict, cancel_event: Optional[threading.Event] = None) -> dict:
        """Execute delegation after confirmation (voice, typed, or GUI approval)."""
        task = confirmation_data["task"]
        project_name = confirmation_data["project_name"]
        project_path = confirmation_data["project_path"]
        label = confirmation_data.get("agent") or "the coding agent"

        if self.dry_run:
            return self._result("delegate_to_coding_agent", False,
                                f"[DRY RUN] Would run {label} in {project_name}: {task}")

        try:
            result_detail = _delegate_to_coding_agent(task, project_name, project_path, self.config,
                                                      cancel_event=cancel_event)
            return self._result("delegate_to_coding_agent", True, result_detail)
        except Exception as e:
            return self._result("delegate_to_coding_agent", False, f"Delegation failed: {e}")

    def _result(self, action_type: str, executed: bool, detail: str) -> dict:
        return {
            "action_type": action_type,
            "executed": executed,
            "dry_run": self.dry_run,
            "detail": detail,
        }


def _send_media_key(key_name: str):
    """Press a media key (src/platform: virtual keys on Windows, system-defined key events and AppleScript's volume
    on macOS)."""
    from src.platform import current
    current().send_media_key(key_name)


def _resolve_project(project_ref: str, agent_cfg: dict, profile: dict) -> dict:
    """Resolve project reference to absolute path.

    Args:
        project_ref: Project name/alias from LLM (may be empty)
        agent_cfg: coding_agent config section (default_project)
        profile: user_profile.json

    Returns:
        dict with:
            success: bool
            path: str (if success)
            project_name: str (if success)
            error: str (if not success)
    """
    projects = profile.get("projects", {})

    if not projects:
        return {
            "success": False,
            "error": f"No project folders are set up yet. Add one under \"projects\" in {paths.profile_file()}"
        }

    # If no project specified, use default
    if not project_ref:
        default_project = agent_cfg.get("default_project", "")
        if not default_project:
            return {
                "success": False,
                "error": "No project specified and no default_project in config"
            }
        if default_project not in projects:
            return {
                "success": False,
                "error": f"Default project '{default_project}' not found in user_profile"
            }
        return {
            "success": True,
            "project_name": default_project,
            "path": projects[default_project]["path"]
        }

    # Try exact match first (case-insensitive)
    project_ref_lower = project_ref.lower()
    for proj_key, proj_cfg in projects.items():
        if proj_key.lower() == project_ref_lower:
            return {
                "success": True,
                "project_name": proj_key,
                "path": proj_cfg["path"]
            }

    # Try alias match
    matches = []
    for proj_key, proj_cfg in projects.items():
        aliases = [a.lower() for a in proj_cfg.get("aliases", [])]
        if project_ref_lower in aliases or any(project_ref_lower in a for a in aliases):
            matches.append((proj_key, proj_cfg["path"]))

    if len(matches) == 0:
        available = ", ".join(projects.keys())
        return {
            "success": False,
            "error": f"Project '{project_ref}' not found. Available: {available}"
        }

    if len(matches) > 1:
        match_names = ", ".join([m[0] for m in matches])
        return {
            "success": False,
            "error": f"Project '{project_ref}' matches multiple: {match_names}. Be more specific"
        }

    return {
        "success": True,
        "project_name": matches[0][0],
        "path": matches[0][1]
    }


def _delegate_to_coding_agent(task: str, project_name: str, project_path: str, config: dict,
                              cancel_event: Optional[threading.Event] = None) -> str:
    """Run the configured coding agent CLI hidden in the project folder and wait for it.

    The agent is coding_agent.cli (Claude Code `claude -p`, Codex `codex exec`, Aider `aider --message`
    or a custom command); the task is appended as the last argument. Output is always saved in Jarvis's
    cache folder, never in the target repo.

    Returns:
        Result detail string (for TTS and logging)
    """
    import datetime

    label, command = coding_agent_command(config)
    agent_cfg = config.get("coding_agent", {}) or {}
    timeout = agent_cfg.get("timeout_seconds", 300)
    capture_output = agent_cfg.get("capture_output", True)

    # Output always in Jarvis's cache folder, keyed by project name
    output_dir_abs = paths.cache_dir() / "coding_agent_outputs" / project_name
    output_dir_abs.mkdir(parents=True, exist_ok=True)

    # Generate output file name
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir_abs / f"{agent_cfg.get('cli', 'agent')}_{timestamp}.txt"

    # One-shot mode is required: an interactive TUI has no terminal when launched from Jarvis.
    cmd = command + [task]

    print(f"[DELEGATE] Spawning {label}: {command[0]}")
    print(f"[DELEGATE] Project: {project_name}")
    print(f"[DELEGATE] Working dir: {project_path}")
    print(f"[DELEGATE] Task: {task}")
    print(f"[DELEGATE] Output: {output_file}")

    try:
        # Coding agent CLIs are console programs: CREATE_NO_WINDOW keeps them from opening a
        # CMD window under pythonw. Popen + poll so a cancel can stop it.
        proc = winproc.popen_hidden(
            cmd,
            cwd=project_path,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out, err = [], []
        readers = []
        if capture_output:
            for stream, sink in ((proc.stdout, out), (proc.stderr, err)):
                t = threading.Thread(target=lambda st=stream, sk=sink: sk.append(st.read()),
                                     name="coding-agent-output", daemon=True)
                t.start()
                readers.append(t)

        deadline = time.time() + timeout
        cancelled = False
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                proc.kill()
                break
            if time.time() > deadline:
                proc.kill()
                proc.wait(timeout=10)
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(0.2)
        proc.wait(timeout=10)
        for t in readers:
            t.join(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        result = subprocess.CompletedProcess(cmd, proc.returncode, "".join(out), "".join(err))

        if cancelled:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"Task: {task}\nTimestamp: {timestamp}\nCANCELLED by user\n\n{result.stdout}")
            return f"{label} delegation cancelled. Partial output saved to {output_file.name}"

        # Save output to file
        output_text = ""
        if capture_output:
            output_text = result.stdout + "\n" + result.stderr
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"Task: {task}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Return code: {result.returncode}\n")
                f.write("=" * 70 + "\n\n")
                f.write(output_text)

        # Generate result detail
        if result.returncode == 0:
            # Extract summary for TTS (first few lines or last line)
            if config.get("summarize_for_tts", True) and output_text:
                lines = [l.strip() for l in output_text.strip().split("\n") if l.strip()]
                if lines:
                    # Use last non-empty line as summary (often the final result)
                    summary = lines[-1]
                    if len(summary) > 100:
                        summary = summary[:97] + "..."
                    detail = f"{label} completed. {summary}"
                else:
                    detail = f"{label} completed. Output saved to {output_file.name}"
            else:
                detail = f"{label} completed. Output saved to {output_file.name}"
        else:
            detail = f"{label} exited with code {result.returncode}. Check {output_file.name}"

        return detail

    except subprocess.TimeoutExpired:
        return f"{label} timed out after {timeout}s"
    except FileNotFoundError:
        return f"{label} not found ({command[0]}). Install it, or choose another coding agent in Settings."
    except Exception as e:
        return f"Delegation failed: {e}"
