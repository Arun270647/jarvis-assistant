"""A diagnostic report for bug reports: `pythonw jarvis_gui.pyw --diagnose` or Settings › Save a report.

Plain text with the OS, Python and package versions, hardware, audio devices, the chosen models and AI
provider (never a key: only where the key comes from), model download status and the last log lines.
Everything goes through redact(), and the Windows user folder and email addresses are masked, because users
attach the file to public issues. Nothing is sent anywhere; the file is saved in the data folder's reports/.
"""
import datetime
import os
import platform
import re
import sys
from importlib import metadata
from pathlib import Path

from src.core import paths
from src.core.redact import redact

PACKAGES = ("PySide6", "faster-whisper", "ctranslate2", "onnxruntime", "sounddevice", "numpy", "piper-tts",
            "claude-agent-sdk", "anthropic", "openai", "jsonschema", "platformdirs", "pywin32", "uiautomation",
            "aiohttp", "torch")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _mask(text: str) -> str:
    text = redact(text)
    home = os.path.expanduser("~")
    if home and home != "~":
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.I)
        text = re.sub(re.escape(home.replace("\\", "/")), "%USERPROFILE%", text, flags=re.I)
    return EMAIL.sub("[email]", text)


def _section(title: str, lines: list) -> str:
    return f"== {title}\n" + "\n".join(f"  {line}" for line in lines) + "\n"


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        return [f"(unavailable: {type(e).__name__}: {e})"]


def _packages() -> list:
    out = []
    for name in PACKAGES:
        try:
            out.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            out.append(f"{name} (not installed)")
    return out


def _hardware() -> list:
    from src.core.hardware import detect
    return [f"{k}: {v}" for k, v in detect().items()]


def _audio() -> list:
    import sounddevice as sd
    default_in, default_out = (sd.default.device if isinstance(sd.default.device, (list, tuple)) else (None, None))
    out = []
    for i, d in enumerate(sd.query_devices()):
        kinds = ("in" if d["max_input_channels"] else "") + ("/out" if d["max_output_channels"] else "")
        marks = (" [default input]" if i == default_in else "") + (" [default output]" if i == default_out else "")
        out.append(f"[{i}] {d['name']} ({sd.query_hostapis(d['hostapi'])['name']}, {kinds.strip('/')}){marks}")
    return out


def _settings(cfg: dict) -> list:
    from src.agent.providers import api_key, role
    from src.core.config_store import assistant_name
    lines = [f"assistant name: {assistant_name(cfg)}"]
    for which in ("main", "utility"):
        try:
            provider, model, s = role(cfg, which)
            key = api_key(provider, s)[1] if s["kind"] != "claude_code" else "n/a (Claude login)"
            lines.append(f"AI {which}: {provider} ({s['kind']}) model={model or '(none)'} key={key or 'none'}")
        except Exception as e:
            lines.append(f"AI {which}: {e}")
    stt, tts = cfg.get("stt", {}), cfg.get("tts", {})
    lines += [f"speech recognition: {stt.get('model_path')} on {stt.get('device')} ({stt.get('compute_type')})",
              f"voice: {tts.get('engine')} (piper voice {tts.get('piper', {}).get('voice')}, fallback "
              f"{tts.get('fallback')}), enabled={tts.get('enabled')}",
              f"microphone index: {cfg.get('audio', {}).get('input_device_index')}",
              f"tools: profile={cfg.get('tools', {}).get('profile')} dry_run={cfg.get('dry_run')} "
              f"full_access={cfg.get('agent', {}).get('full_access')} coding_agent={cfg.get('coding_agent', {}).get('cli')}",
              f"phone app: {cfg.get('phone', {}).get('enabled')}",
              f"data folder: {paths.home()} | models: {paths.models_dir()}"]
    return lines


def _models() -> list:
    from src.core import models
    out = []
    for g in models.groups():
        for o in g["options"]:
            st = models.status(o)
            if st["state"] != "missing":
                out.append(f"{g['id']}/{o['id']}: {st['state']} ({st['have']} of {st['total']} bytes)")
    return out or ["(no catalogue models on disk)"]


def _claude_code(cfg: dict) -> list:
    from src.core import winproc
    cli = ((cfg.get("llm", {}) or {}).get("providers", {}) or {}).get("claude_code", {}).get("cli_path") or "claude"
    res = winproc.run_hidden([cli, "--version"], timeout=15, capture_output=True, text=True)
    return [(res.stdout or res.stderr or f"exit {res.returncode}").strip()[:200]]


def _log(n: int) -> list:
    from src.core import logging_setup
    return [line.rstrip("\n") for line in logging_setup.tail(n)] or ["(empty)"]


def build_report(log_lines: int = 200) -> str:
    from src.core.config_store import load_config
    cfg = load_config()
    parts = [f"Jarvis diagnostic report, created {datetime.datetime.now().isoformat(timespec='seconds')}\n",
             _section("System", [f"{platform.platform()}", f"Python {sys.version.split()[0]} ({sys.executable})"]),
             _section("Packages", _safe(_packages)),
             _section("Hardware", _safe(_hardware)),
             _section("Audio devices", _safe(_audio)),
             _section("Settings", _safe(_settings, cfg)),
             _section("Models on disk", _safe(_models)),
             _section("Claude Code", _safe(_claude_code, cfg)),
             _section(f"Last {log_lines} log lines", _safe(_log, log_lines))]
    return _mask("\n".join(parts))


def save_report() -> Path:
    folder = paths.home() / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"jarvis-report-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    path.write_text(build_report(), encoding="utf-8")
    return path
