"""Build the release manifest: release/manifest.json (version + file hashes)."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    version_file = ROOT / "VERSION"
    if not version_file.exists():
        print("BUILD FAILED: VERSION file is missing (see README deployment checklist)")
        return 2
    version = version_file.read_text(encoding="utf-8").strip()
    if not version:
        print("BUILD FAILED: VERSION file is empty")
        return 2
    files = {}
    for p in sorted((ROOT / "shop").glob("*.py")):
        files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    out = ROOT / "release" / "manifest.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"name": "demo-shop", "version": version, "files": files}, indent=2), encoding="utf-8")
    print(f"Built release {version}: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
