"""Executors behind the open_app, media_control, system_command and delegate_to_claude tools.

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

    def _handle_delegate(self, params: dict) -> dict:
        task = params.get("task") or params.get("prompt") or ""
        project_ref = params.get("project", "").strip()

        if not task.strip():
            return self._result("delegate_to_claude", False, "No task specified for delegation")

        if self.dry_run:
            return self._result("delegate_to_claude", False,
                                f"[DRY RUN] Would delegate to Claude Code: {task}")

        # Check if Claude Code is enabled
        claude_cfg = self.config.get("claude_code", {})
        if not claude_cfg.get("enabled", False):
            return self._result("delegate_to_claude", False,
                                "Claude Code delegation disabled in config")

        # Resolve project reference to absolute path
        project_resolution = _resolve_project(project_ref, claude_cfg, self.profile)

        if not project_resolution["success"]:
            # Return error for TTS to speak
            return self._result("delegate_to_claude", False, project_resolution["error"])

        project_name = project_resolution["project_name"]
        project_path = project_resolution["path"]

        # Rule 10: Require confirmation before launching (if enabled)
        if claude_cfg.get("require_confirmation", True):
            return {
                "action_type": "delegate_to_claude",
                "executed": False,
                "dry_run": self.dry_run,
                "detail": f"Run Claude in {project_name}?",
                "requires_confirmation": True,
                "confirmation_data": {
                    "task": task,
                    "project_name": project_name,
                    "project_path": project_path,
                }
            }

        # No confirmation required — delegate directly
        try:
            result_detail = _delegate_to_claude_code(task, project_name, project_path, claude_cfg)
            return self._result("delegate_to_claude", True, result_detail)
        except Exception as e:
            return self._result("delegate_to_claude", False, f"Delegation failed: {e}")

    def resolve_delegation(self, params: dict) -> dict:
        """Resolve a delegate_to_claude request without running it.

        Returns {"success": True, "task", "project_name", "project_path"} or
        {"success": False, "error": str}.
        """
        task = (params.get("task") or params.get("prompt") or "").strip()
        if not task:
            return {"success": False, "error": "No task specified for delegation"}
        claude_cfg = self.config.get("claude_code", {})
        if not claude_cfg.get("enabled", False):
            return {"success": False, "error": "Claude Code delegation disabled in config"}
        res = _resolve_project((params.get("project") or "").strip(), claude_cfg, self.profile)
        if not res["success"]:
            return res
        return {"success": True, "task": task, "project_name": res["project_name"], "project_path": res["path"]}

    def execute_confirmed_delegation(self, confirmation_data: dict, cancel_event: Optional[threading.Event] = None) -> dict:
        """Execute delegation after confirmation (voice, typed, or GUI approval)."""
        task = confirmation_data["task"]
        project_name = confirmation_data["project_name"]
        project_path = confirmation_data["project_path"]

        claude_cfg = self.config.get("claude_code", {})

        if self.dry_run:
            return self._result("delegate_to_claude", False,
                                f"[DRY RUN] Would run Claude Code in {project_name}: {task}")

        try:
            result_detail = _delegate_to_claude_code(task, project_name, project_path, claude_cfg,
                                                     cancel_event=cancel_event)
            return self._result("delegate_to_claude", True, result_detail)
        except Exception as e:
            return self._result("delegate_to_claude", False, f"Delegation failed: {e}")

    def _result(self, action_type: str, executed: bool, detail: str) -> dict:
        return {
            "action_type": action_type,
            "executed": executed,
            "dry_run": self.dry_run,
            "detail": detail,
        }


def _send_media_key(key_name: str):
    """Send a media key press via Windows API."""
    import ctypes

    VK_MAP = {
        "play_pause": 0xB3,
        "stop": 0xB2,
        "next_track": 0xB0,
        "prev_track": 0xB1,
        "volume_up": 0xAF,
        "volume_down": 0xAE,
        "volume_mute": 0xAD,
    }

    vk = VK_MAP.get(key_name)
    if vk is None:
        raise ValueError(f"Unknown media key: {key_name}")

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def _resolve_project(project_ref: str, claude_cfg: dict, profile: dict) -> dict:
    """Resolve project reference to absolute path.

    Args:
        project_ref: Project name/alias from LLM (may be empty)
        claude_cfg: claude_code config section
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
        default_project = claude_cfg.get("default_project", "")
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


def _delegate_to_claude_code(task: str, project_name: str, project_path: str, config: dict,
                             cancel_event: Optional[threading.Event] = None) -> str:
    """Delegate a task to Claude Code CLI.

    Spawns claude CLI in the target project directory.
    Output is always saved in Jarvis's cache folder, never in the target repo.

    Args:
        task: The prompt/task to give to Claude Code
        project_name: Project key from user_profile.json
        project_path: Absolute path to project directory
        config: claude_code config section

    Returns:
        Result detail string (for TTS and logging)
    """
    import datetime

    cli_path = config.get("cli_path", "claude")
    timeout = config.get("timeout_seconds", 300)
    capture_output = config.get("capture_output", True)

    # Output always in Jarvis's cache folder, keyed by project name
    output_dir_abs = paths.cache_dir() / "claude_outputs" / project_name
    output_dir_abs.mkdir(parents=True, exist_ok=True)

    # Generate output file name
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir_abs / f"claude_{timestamp}.txt"

    # Build command. Print mode (-p) is required: without it the CLI starts its
    # interactive TUI, which has no terminal when launched from Jarvis.
    cmd = [cli_path] + list(config.get("cli_args", ["-p"])) + [task]

    print(f"[DELEGATE] Spawning Claude Code CLI...")
    print(f"[DELEGATE] Project: {project_name}")
    print(f"[DELEGATE] Working dir: {project_path}")
    print(f"[DELEGATE] Task: {task}")
    print(f"[DELEGATE] Output: {output_file}")

    try:
        # claude.exe is a console program: CREATE_NO_WINDOW keeps it from opening a
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
                                     name="claude-delegate-output", daemon=True)
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
        result = subprocess.CompletedProcess(cmd, proc.returncode, "".join(out), "".join(err))

        if cancelled:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"Task: {task}\nTimestamp: {timestamp}\nCANCELLED by user\n\n{result.stdout}")
            return f"Claude delegation cancelled. Partial output saved to {output_file.name}"

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
                    detail = f"Claude completed. {summary}"
                else:
                    detail = f"Claude completed. Output saved to {output_file.name}"
            else:
                detail = f"Claude completed. Output saved to {output_file.name}"
        else:
            detail = f"Claude exited with code {result.returncode}. Check {output_file.name}"

        return detail

    except subprocess.TimeoutExpired:
        return f"Claude timed out after {timeout}s"
    except FileNotFoundError:
        return f"Claude Code CLI not found at: {cli_path}. Install: npm install -g @anthropic-ai/claude-code"
    except Exception as e:
        return f"Delegation failed: {e}"
