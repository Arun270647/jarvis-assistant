"""Where the agent may read, write and run things.

- Authorized roots: the project folders in user_profile.json plus any extra
  agent.workspace_roots. Every file/command tool resolves paths inside these.
- Protected paths: Jarvis's own code and config (the app folder; anything listed
  in agent.protected_paths) and its data folders (settings, profile, database,
  logs, models, cache: src.core.paths.data_dirs()) are readable but never
  writable by the agent, so it cannot weaken its own policy.
- Secret files (.env, keys, credential stores) are neither read nor written.
- Commands: argv (never a shell) checked against a deny-list, a global
  allow-list of read-only commands, and per-project allow-lists from
  user_profile.json ("commands": [...]). Anything else needs approval.
- agent.full_access: any path on the PC is authorized (relative paths without a
  project resolve against the user's home folder) and every command that is not
  denied runs without approval. Secret files, protected paths, per-project rules
  and the deny-list still apply.
"""
import fnmatch
import os
import re
from pathlib import Path
from typing import Optional

from src.core import paths

JARVIS_ROOT = paths.APP_ROOT                        # this assistant's own code

SECRET_GLOBS = [".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*", "*.kdbx",
                "credentials*", "*secrets*", ".netrc", ".npmrc", ".pypirc", "*.keystore"]

DEFAULT_PROTECTED = ["src", "config", "tests", "tools_ext", "scripts", "jarvis_gui.pyw", "requirements.txt",
                     ".git", ".claude",
                     "sandbox/_pristine", "sandbox/reset_demo.py"]

# Read-only commands that are always fine inside an authorized root.
DEFAULT_ALLOWED_COMMANDS = [
    "git status", "git diff", "git log", "git show", "git branch", "git rev-parse", "git ls-files",
    "python --version", "node --version", "npm --version",
]
# The user's everyday git workflow: allowed inside a planned task (pushing is not here: it always asks).
WORKFLOW_COMMANDS = [
    "git add", "git commit", "git switch", "git checkout -b", "git fetch", "git stash list",
    # dev servers (run with background=true, stopped with stop_process)
    "npm run dev", "npm run preview", "yarn start", "yarn dev",
]
# Never run, regardless of approval.
DENIED_COMMANDS = [
    "format", "diskpart", "cipher", "bcdedit", "reg delete", "reg add", "vssadmin", "takeown", "icacls",
    "shutdown", "rmdir /s", "rd /s", "del /s", "del /q", "rm -rf", "rm -r", "git push --force", "git push -f",
    "git clean", "git reset --hard", "net user", "powershell", "pwsh", "cmd", "wsl", "bash", "curl", "wget",
    "certutil", "bitsadmin", "mshta", "rundll32", "regsvr32", "schtasks", "sc",
]


class WorkspaceError(Exception):
    pass


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.resolve()))


def _is_within(path: Path, root: Path) -> bool:
    a, b = _norm(path), _norm(root)
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


class WorkspacePolicy:
    def __init__(self, profile: dict, config: dict):
        self.profile = profile
        acfg = config.get("agent", {})
        self.full_access = bool(acfg.get("full_access", False))
        self.projects = {k: Path(v["path"]) for k, v in profile.get("projects", {}).items() if v.get("path")}
        self.roots = list(self.projects.values()) + [Path(r) if Path(r).is_absolute() else JARVIS_ROOT / r
                                                      for r in acfg.get("workspace_roots", [])]
        protected = acfg.get("protected_paths", DEFAULT_PROTECTED)
        self.protected = ([JARVIS_ROOT / p for p in protected] + paths.data_dirs()
                          + [Path(p) for p in acfg.get("extra_protected", [])])
        self.allowed_commands = DEFAULT_ALLOWED_COMMANDS + list(acfg.get("allowed_commands", []))
        projects = profile.get("projects", {})
        self.project_commands = {k: list(v.get("commands", [])) for k, v in projects.items()}
        self.project_denied = {k: list(v.get("deny_commands", [])) for k, v in projects.items()}
        self.project_protected = {k: list(v.get("protected", [])) for k, v in projects.items()}
        self.project_read_only = {k for k, v in projects.items() if v.get("read_only")}
        self.commit_branches = {k: list(v.get("commit_branches", [])) for k, v in projects.items() if v.get("commit_branches")}

    # ------------------------------------------------------------ paths

    def resolve(self, path: str, project: Optional[str] = None) -> Path:
        """Absolute path inside an authorized root. Relative paths resolve against the project."""
        if not path or not str(path).strip():
            raise WorkspaceError("Empty path")
        p = Path(os.path.expandvars(str(path).strip()))
        if not p.is_absolute():
            base = self.projects.get(project) if project else None
            if base is None and self.full_access:
                base = Path.home()          # "Desktop", "Downloads/report.pdf" ...
            if base is None:
                if not self.projects:
                    raise WorkspaceError(self.no_projects_message())
                if len(self.projects) == 1:
                    base = next(iter(self.projects.values()))
                else:
                    raise WorkspaceError(f"Relative path '{path}' needs a project ({', '.join(self.projects)}) "
                                         "or use an absolute path")
            p = base / p
        p = Path(os.path.abspath(p))
        if not self.full_access and not any(_is_within(p, r) for r in self.roots):
            if not self.roots:
                raise WorkspaceError(self.no_projects_message())
            raise WorkspaceError(f"'{p}' is outside the authorized folders: "
                                 + ", ".join(str(r) for r in self.roots))
        if self.is_secret(p):
            raise WorkspaceError(f"'{p.name}' looks like a secrets/credentials file; Jarvis does not open those")
        return p

    def check_writable(self, p: Path) -> None:
        for prot in self.protected:
            if _is_within(p, prot):
                raise WorkspaceError(f"'{p}' is protected (Jarvis's own code/config/data); the agent cannot modify it")
        project = self.project_of(p)
        if project is None:
            return
        if project in self.project_read_only:
            raise WorkspaceError(f"Project '{project}' is read-only (reference material); the agent cannot modify it")
        root = self.projects[project]
        rel = Path(os.path.relpath(p, root)).as_posix()
        for pattern in self.project_protected.get(project, []):
            pat = pattern.strip("/")
            if rel == pat or rel.startswith(pat + "/") or fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat):
                raise WorkspaceError(f"'{rel}' is protected in project '{project}' (user_profile.json rule); "
                                     "the agent cannot modify it")

    @staticmethod
    def no_projects_message() -> str:
        return ("No project folders are set up yet, so Jarvis cannot work with files. The user can add one under "
                f"\"projects\" in {paths.profile_file()} (each with a \"path\"), then ask again.")

    @staticmethod
    def is_secret(p: Path) -> bool:
        name = p.name.lower()
        return any(fnmatch.fnmatch(name, g) for g in SECRET_GLOBS)

    def project_of(self, p: Path) -> Optional[str]:
        best = None
        for name, root in self.projects.items():
            if _is_within(p, root) and (best is None or len(str(root)) > len(str(self.projects[best]))):
                best = name
        return best

    def describe_roots(self) -> str:
        roots = [f"{k} = {v}" for k, v in self.projects.items()]
        if self.full_access:
            roots.append(f"home = {Path.home()} (full PC access: any path)")
        return "; ".join(roots)

    # ------------------------------------------------------------ commands

    def is_read_only_command(self, argv: list) -> bool:
        """Commands on the global read-only list (git status, versions ...) can run outside a task."""
        exe = re.sub(r"\.(exe|cmd|bat|com)$", "", Path(argv[0]).name.lower()) if argv else ""
        words = [exe] + [a.lower() for a in argv[1:]]
        return any(words[:len(p.split())] == p.lower().split() for p in DEFAULT_ALLOWED_COMMANDS)

    def branch_problem(self, argv: list, cwd: Path) -> Optional[str]:
        """Projects with commit_branches: commits/pushes only from those branches."""
        project = self.project_of(cwd)
        allowed = self.commit_branches.get(project) if project else None
        words = [a.lower() for a in argv[:2]]
        if not allowed or len(words) < 2 or Path(words[0]).stem != "git" or words[1] not in ("commit", "push", "merge", "rebase"):
            return None
        from src.core.winproc import run_hidden
        try:
            r = run_hidden(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(cwd), capture_output=True, text=True, timeout=15)
            branch = r.stdout.strip()
        except Exception as e:
            return f"Could not read the current branch: {e}"
        if branch not in allowed:
            return (f"Project '{project}' only allows {words[1]} on {', '.join(allowed)}; the current branch is "
                    f"'{branch}'. Switch branches first (or ask the user).")
        return None

    def classify_command(self, argv: list, cwd: Path) -> str:
        """'deny' | 'allow' | 'ask' for an argv list run in cwd (already authorized)."""
        if not argv:
            return "deny"
        line = " ".join(argv).lower()
        exe = Path(argv[0]).name.lower()
        exe = re.sub(r"\.(exe|cmd|bat|com)$", "", exe)
        norm = " ".join([exe] + [a.lower() for a in argv[1:]])
        for bad in DENIED_COMMANDS:
            if norm == bad or norm.startswith(bad + " ") or exe == bad:
                return "deny"
        if any(ch in line for ch in ("&&", "||", "|", ">", "<", "`", "$(")):
            return "deny"   # no shell is used, but refuse anything that looks like shell chaining
        project = self.project_of(cwd)
        words = [exe] + [a.lower() for a in argv[1:]]
        for bad in (self.project_denied.get(project, []) if project else []):
            if words[:len(bad.split())] == bad.lower().split():
                return "deny"
        if self.full_access:
            return "allow"
        allowed = self.allowed_commands + WORKFLOW_COMMANDS + (self.project_commands.get(project, []) if project else [])
        for prefix in allowed:
            pf = prefix.lower().split()
            if [exe] + [a.lower() for a in argv[1:len(pf)]] == [re.sub(r"\.(exe|cmd|bat)$", "", pf[0])] + pf[1:]:
                return "allow"
        return "ask"
