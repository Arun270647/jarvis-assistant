"""Check every link Jarvis depends on: model downloads, model pages, add-on packages, the build's pinned downloads,
the AI providers' addresses and the links in the app's code. Run it before a release.

    python scripts/check_links.py           # a table of every link, exit 1 if any is broken
    python scripts/check_links.py --quiet   # only the broken ones

- Model files (config/models.json) and the build's downloads (packaging/runtime.json) must answer, and their size
  must match the catalogue (one byte is requested, the size comes from the server's headers).
- Pages, licenses and voice samples must answer without an error.
- Add-on packages must exist on PyPI in the pinned version.
- A cloud provider's API address counts as reachable when it answers at all: without a key it says 401 or 404.
- Links written in the code (src/) must answer without an error. Local addresses are skipped.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "Jarvis link check"
LOCAL = re.compile(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])", re.I)
CODE_URL = re.compile(r"https://[^\s\"'`<>)\]{}|\\]+")
IGNORE_IN_CODE = ("example.com", "example.org", "www.w3.org/2000/svg")


def collect(root: Path = ROOT) -> list:
    """Every link to check: {"kind", "url", "source"} plus "size" for downloads."""
    links = []
    catalogue = json.loads((root / "config" / "models.json").read_text(encoding="utf-8"))
    for group in catalogue["groups"]:
        for option in group["options"]:
            source = f"models.json {option['id']}"
            for f in option.get("files", []):
                links.append({"kind": "download", "url": f["url"], "size": f["size"], "source": source})
            for key in ("page", "license_url", "sample"):
                if option.get(key):
                    links.append({"kind": "page", "url": option[key], "source": f"{source} {key}"})
    for name, addon in catalogue.get("addons", {}).items():
        if addon.get("page"):
            links.append({"kind": "page", "url": addon["page"], "source": f"add-on {name}"})
        for package in addon["packages"]:
            if "==" in package:
                pkg, version = package.split("==", 1)
                links.append({"kind": "pypi", "url": f"https://pypi.org/pypi/{pkg}/{version}/json",
                              "source": f"add-on {name} {package}"})
    runtime = root / "packaging" / "runtime.json"
    if runtime.exists():
        for name, pin in json.loads(runtime.read_text(encoding="utf-8")).items():
            if isinstance(pin, dict) and pin.get("url"):
                links.append({"kind": "download", "url": pin["url"], "size": pin.get("size"), "source": f"runtime.json {name}"})
    defaults = json.loads((root / "config" / "defaults.json").read_text(encoding="utf-8"))
    for name, provider in ((defaults.get("llm", {}) or {}).get("providers", {}) or {}).items():
        base = provider.get("base_url") or ""
        if base.startswith("https://") and not LOCAL.match(base):
            links.append({"kind": "api", "url": base.rstrip("/") + "/models", "source": f"provider {name}"})
    seen = {link["url"] for link in links}
    for path in sorted((root / "src").rglob("*")):
        if path.suffix not in (".py", ".js", ".html") or "__pycache__" in path.parts:
            continue
        for url in CODE_URL.findall(path.read_text(encoding="utf-8", errors="replace")):
            url = url.rstrip(".,;:")
            if url not in seen and not LOCAL.match(url) and not any(i in url for i in IGNORE_IN_CODE):
                seen.add(url)
                links.append({"kind": "page", "url": url, "source": path.relative_to(root).as_posix()})
    return links


def _status(error: Exception) -> Optional[int]:
    return error.code if isinstance(error, urllib.error.HTTPError) else None


def check(link: dict, opener: Callable = urllib.request.urlopen) -> dict:
    """{"ok", "status", "detail"} for one link."""
    url, kind = link["url"], link["kind"]
    headers = {"User-Agent": USER_AGENT}
    if kind == "download":
        headers["Range"] = "bytes=0-0"
    try:
        with opener(urllib.request.Request(url, headers=headers), timeout=30) as r:
            status = getattr(r, "status", 200)
            if kind == "download" and link.get("size"):
                total = (r.headers.get("Content-Range") or "").rpartition("/")[2] if status == 206 else \
                    r.headers.get("Content-Length")
                if str(total).isdigit() and int(total) != int(link["size"]):
                    return {"ok": False, "status": status, "detail": f"size {total}, catalogue says {link['size']}"}
            return {"ok": True, "status": status, "detail": ""}
    except Exception as e:
        status = _status(e)
        if kind == "api" and status is not None and status < 500:
            return {"ok": True, "status": status, "detail": "reachable (answers without a key)"}
        return {"ok": False, "status": status, "detail": str(e)[:160]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quiet", action="store_true", help="print only the broken links")
    args = ap.parse_args(argv)
    links = collect()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, links))
    broken = 0
    for link, result in zip(links, results):
        broken += not result["ok"]
        if result["ok"] and args.quiet:
            continue
        mark = "ok  " if result["ok"] else "FAIL"
        print(f"{mark} {result['status'] or '---'} {link['kind']:<8} {link['url']}  ({link['source']}) {result['detail']}")
    print(f"\n{len(links)} links, {broken} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
