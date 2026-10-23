"""What this computer has, for choosing the speech model (setup wizard, Settings › Models, --diagnose).

detect() returns plain facts; config/models.json turns them into recommendations with rules such as
"nvidia_free_vram_gb >= 3 and cuda_libs" (see matches()). Every probe is best effort and quick: a missing
tool or driver simply means "not found".

Facts: os, cpu_logical_cores, ram_gb, nvidia_gpu, gpu_name, nvidia_vram_gb, nvidia_free_vram_gb, cuda_libs
(cuBLAS and cuDNN for CUDA 12 can be found, which faster-whisper needs to use the graphics card).
"""
import ctypes
import glob
import os
import platform
import re
import sys
from typing import Optional

CUDA_LIBS = {"windows": (("cublas64_12.dll",), ("cudnn64_9.dll", "cudnn_ops64_9.dll"))}


def _ram_gb() -> Optional[float]:
    if sys.platform != "win32":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / 1024 ** 3, 1)
    except Exception:
        pass
    return None


def _nvidia() -> dict:
    """GPU name and memory from nvidia-smi (installed with the driver); {} without an NVIDIA GPU."""
    from src.core import winproc
    try:
        res = winproc.run_hidden(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                                  "--format=csv,noheader,nounits"], timeout=8, capture_output=True, text=True)
    except Exception:
        return {}
    if res.returncode != 0 or not res.stdout.strip():
        return {}
    first = res.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    try:
        return {"gpu_name": parts[0], "nvidia_vram_gb": round(float(parts[1]) / 1024, 1),
                "nvidia_free_vram_gb": round(float(parts[2]) / 1024, 1)}
    except (IndexError, ValueError):
        return {"gpu_name": parts[0]}


def _search_dirs() -> list:
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for site in sys.path:          # pip's nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels
        dirs += glob.glob(os.path.join(site, "nvidia", "*", "bin"))
    return dirs


def cuda_libs_found() -> bool:
    groups = CUDA_LIBS.get("windows" if sys.platform == "win32" else "", ())
    if not groups:
        return False
    dirs = _search_dirs()
    return all(any(os.path.isfile(os.path.join(d, name)) for d in dirs for name in names) for names in groups)


def detect() -> dict:
    facts = {"os": f"{platform.system()} {platform.release()} ({platform.version()})",
             "cpu_logical_cores": os.cpu_count() or 1, "ram_gb": _ram_gb(),
             "nvidia_gpu": False, "gpu_name": None, "nvidia_vram_gb": 0.0, "nvidia_free_vram_gb": 0.0,
             "cuda_libs": False}
    gpu = _nvidia()
    if gpu:
        facts.update(gpu, nvidia_gpu=True)
        facts["cuda_libs"] = cuda_libs_found()
    return facts


# ---------------------------------------------------------------------- recommendation rules

_COMPARISON = re.compile(r"^([a-z_]+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)$")
_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def matches(rule: str, facts: dict) -> bool:
    """A rule from models.json: "always", a fact name ("nvidia_gpu"), a comparison
    ("cpu_logical_cores >= 8"), optionally joined with "and". Unknown facts count as false."""
    for term in (rule or "").split(" and "):
        term = term.strip()
        if term == "always":
            continue
        m = _COMPARISON.match(term)
        if m:
            value = facts.get(m.group(1))
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not _OPS[m.group(2)](value, float(m.group(3))):
                return False
        elif not (re.fullmatch(r"[a-z_]+", term) and facts.get(term) is True):
            return False
    return True
