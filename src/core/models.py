"""The model catalogue (config/models.json): what each model does, which option suits this computer, which
settings choosing it changes, and whether its files are on disk.

    recommend(facts)             {group id: {"option", "reason", "settings"}} for hardware.detect() facts
    settings_for(group, option, facts)   the settings to save when the user picks an option
    status(option)               installed | missing | partial | builtin, with byte counts; an option that needs an
                                 add-on (src/core/addons.py) is only installed once the add-on is too
"""
import json
from functools import lru_cache
from pathlib import Path

from src.core import paths
from src.core.hardware import matches

CATALOGUE_PATH = paths.shipped_config_dir() / "models.json"


@lru_cache(maxsize=1)
def catalogue() -> dict:
    with open(CATALOGUE_PATH, encoding="utf-8") as f:
        return json.load(f)


def groups() -> list:
    return catalogue()["groups"]


def group(group_id: str) -> dict:
    for g in groups():
        if g["id"] == group_id:
            return g
    raise KeyError(f"No model group {group_id!r}")


def find(option_id: str) -> tuple:
    """(group, option) for an option id."""
    for g in groups():
        for o in g["options"]:
            if o["id"] == option_id:
                return g, o
    raise KeyError(f"No model {option_id!r}")


def text(value: str, name: str) -> str:
    return str(value or "").replace("{name}", name)


def _rule(g: dict, facts: dict) -> dict:
    for rule in g.get("recommend", []):
        if matches(rule.get("when", ""), facts):
            return rule
    return {}


def recommend(facts: dict) -> dict:
    out = {}
    for g in groups():
        rule = _rule(g, facts)
        if rule:
            out[g["id"]] = {"option": rule["option"], "reason": rule.get("reason", ""),
                            "settings": settings_for(g["id"], rule["option"], facts)}
    return out


def settings_for(group_id: str, option_id: str, facts: dict) -> dict:
    """The option's own settings plus the hardware ones of the group's matching rule (for speech recognition:
    the device and precision suit the computer whichever size the user picks)."""
    g, o = find(option_id)
    if g["id"] != group_id:
        raise KeyError(f"{option_id} is not a {group_id} model")
    settings = dict(_rule(g, facts).get("config", {}))
    settings.update(o.get("config", {}))
    return settings


def delete(option: dict) -> int:
    """Remove an option's downloaded files (and unfinished parts). Returns the bytes freed."""
    freed = 0
    for f in option.get("files") or []:
        dest = target(option, f)
        for p in (dest, dest.with_name(dest.name + ".part")):
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
    folder = paths.models_dir() / option.get("dir", "")
    if option.get("dir") and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
    return freed


def target(option: dict, file: dict) -> Path:
    return paths.models_dir() / option.get("dir", "") / file["name"]


def status(option: dict) -> dict:
    files = option.get("files") or []
    total = sum(f["size"] for f in files)
    if not files:
        return {"state": "builtin", "have": 0, "total": 0}
    have, complete = 0, 0
    for f in files:
        dest = target(option, f)
        part = dest.with_name(dest.name + ".part")
        if dest.exists() and dest.stat().st_size == f["size"]:
            have += f["size"]
            complete += 1
        elif part.exists():
            have += min(part.stat().st_size, f["size"])
    state = "installed" if complete == len(files) else "partial" if have else "missing"
    out = {"state": state, "have": have, "total": total}
    if option.get("addon"):
        from src.core import addons
        out["addon"] = option["addon"]
        out["addon_installed"] = addons.installed(option["addon"])
        if state == "installed" and not out["addon_installed"]:
            out["state"] = "partial"
    return out
