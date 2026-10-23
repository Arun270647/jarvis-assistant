"""Controlled tool extensions.

A capability that Jarvis lacks can be added as a Python module in tools_ext/
that defines `register(registry, config)` and calls registry.register(ToolSpec(...)).
A module is only loaded when the user lists it in the settings
`tools.extensions` (Settings shows them). tools_ext/ is a protected path, so
the agent itself cannot write or enable extensions, and every extension tool
still goes through PolicyExecutor. Extension tools default to "ask" unless
the extension sets a permission and the user keeps it.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXT_DIR = ROOT / "tools_ext"


def available_extensions() -> list:
    return sorted(p.stem for p in EXT_DIR.glob("*.py") if not p.name.startswith("_")) if EXT_DIR.exists() else []


def load_extensions(reg, config: dict) -> list:
    enabled = (config.get("tools", {}) or {}).get("extensions", []) or []
    loaded = []
    for name in enabled:
        path = EXT_DIR / f"{name}.py"
        if not path.is_file():
            print(f"[TOOLS] Extension '{name}' enabled but {path} does not exist")
            continue
        before = set(reg.names())
        try:
            spec = importlib.util.spec_from_file_location(f"jarvis_ext_{name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.register(reg, config)
        except Exception as e:
            print(f"[TOOLS] Extension '{name}' failed to load: {e}")
            continue
        for tool in set(reg.names()) - before:
            s = reg.get(tool)
            if s.permission == "allow" and s.risk != "safe":
                s.permission = "ask"
            loaded.append(tool)
        print(f"[TOOLS] Extension '{name}' loaded: {sorted(set(reg.names()) - before)}")
    return loaded
