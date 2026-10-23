"""Reset sandbox/demo_shop to its starting state (one pricing bug, no VERSION file, no release).

    python sandbox/reset_demo.py
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "_pristine" / "demo_shop"
DST = HERE / "demo_shop"

if __name__ == "__main__":
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"Reset {DST}")
