"""Write THIRD_PARTY_LICENSES.md: every package Jarvis installs (requirements.txt and what those pull in, as
installed in this Python), and the bundled fonts and models.

    python scripts/third_party_licenses.py            # rewrite the file
    python scripts/third_party_licenses.py --check    # exit 1 if a core requirement is missing from it

Run it before each release, from the environment the release is built from. Licenses come from each package's
own metadata; a package whose metadata says nothing useful is listed as "see project page". Packages that only the
other OS installs (pywin32 on Windows, PyObjC on macOS) can't be read here: they are taken from requirements.lock,
with their licenses from OTHER_OS_LICENSES.
"""
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "THIRD_PARTY_LICENSES.md"
OS_NAMES = {"win32": "Windows", "darwin": "macOS"}

# Licenses of the packages only one OS installs (from each package's own metadata), for the list made on the other
# OS. A package missing here is listed as "see project page".
OTHER_OS_LICENSES = {
    "pywin32": ("PSF", "https://github.com/mhammond/pywin32"),
    "uiautomation": ("Apache 2.0", "https://github.com/yinkaisheng/Python-UIAutomation-for-Windows"),
    "comtypes": ("MIT", "https://github.com/enthought/comtypes"),
    "colorama": ("BSD License", "https://github.com/tartley/colorama"),
    "pyobjc-core": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "pyobjc-framework-*": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "keyring": ("MIT", "https://github.com/jaraco/keyring"),
    "jaraco-classes": ("MIT", "https://github.com/jaraco/jaraco.classes"),
    "jaraco-context": ("MIT", "https://github.com/jaraco/jaraco.context"),
    "jaraco-functools": ("MIT", "https://github.com/jaraco/jaraco.functools"),
    "more-itertools": ("MIT", "https://github.com/more-itertools/more-itertools"),
    "backports-tarfile": ("MIT", "https://github.com/jaraco/backports.tarfile"),
    "importlib-metadata": ("Apache-2.0", "https://github.com/python/importlib_metadata"),
    "zipp": ("MIT", "https://github.com/jaraco/zipp"),
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level(path: Path) -> list:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(canonical(re.split(r"[<>=!~\[; ]", line, 1)[0]))
    return names


def _needed(req: str) -> str:
    """The distribution name a requirement string names, or "" when it only applies to an extra or another
    platform / Python."""
    try:
        from packaging.requirements import Requirement
        r = Requirement(req)
        if r.marker is not None and not r.marker.evaluate({"extra": ""}):
            return ""
        return canonical(r.name)
    except Exception:
        if "extra ==" in req:
            return ""
        return canonical(re.split(r"[<>=!~\[; (]", req, 1)[0])


def closure(names: list) -> dict:
    """{canonical name: Distribution} for the names and everything they require, as installed."""
    found, todo = {}, list(names)
    while todo:
        name = todo.pop()
        if name in found:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        found[name] = dist
        todo.extend(n for n in (_needed(r) for r in dist.requires or []) if n)
    return found


def other_os_packages() -> dict:
    """{OS name: [(name, version)]} for the packages in requirements.lock that only another OS installs."""
    from packaging.markers import Marker
    python = {"python_full_version": "3.11.9", "python_version": "3.11"}     # include the ones for older Pythons
    here = sys.platform
    out = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+)==([^\s;\\]+)\s*;\s*([^\\]+?)\s*\\?$", line)
        if not m:
            continue
        marker = Marker(m.group(3))
        if marker.evaluate(dict(python, sys_platform=here)):
            continue
        for platform, label in OS_NAMES.items():
            if platform != here and marker.evaluate(dict(python, sys_platform=platform)):
                out.setdefault(label, []).append((canonical(m.group(1)), m.group(2)))
    return out


def known_license(name: str) -> tuple:
    if name in OTHER_OS_LICENSES:
        return OTHER_OS_LICENSES[name]
    if name.startswith("pyobjc-framework-"):
        return OTHER_OS_LICENSES["pyobjc-framework-*"]
    return "see project page", f"https://pypi.org/project/{name}/"


def license_of(dist) -> str:
    meta = dist.metadata
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c not in ("OSI Approved",)]
    text = (meta.get("License") or "").strip()
    if text and len(text) <= 60 and "\n" not in text:
        return text
    if classifiers:
        return " / ".join(dict.fromkeys(classifiers))
    return "see project page"


def homepage(dist) -> str:
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"]
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository", "source code"):
            return url.strip()
    return f"https://pypi.org/project/{dist.metadata['Name']}/"


def cell(text: str) -> str:
    return str(text).replace("|", "\\|")


def render() -> str:
    import json
    runtime = json.loads((ROOT / "packaging" / "runtime.json").read_text(encoding="utf-8"))
    core = top_level(ROOT / "requirements.txt")
    dists = closure(core)
    missing = [n for n in core if n not in dists]
    lines = [
        "# Third-party licenses",
        "",
        "Jarvis is licensed under the Apache License 2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). It uses the",
        "packages below, each under its own license. Generated by `scripts/third_party_licenses.py` from the",
        "packages' own metadata; check a package's page when its license matters to you.",
        "",
        "## Python packages",
        "",
        "Installed by `requirements.txt` (\"yes\" in the last column) and what those require.",
        "",
        "| Package | Version | License | Project | In requirements.txt |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(dists):
        d = dists[name]
        lines.append(f"| {name} | {d.version} | {cell(license_of(d))} | {homepage(d)} | {'yes' if name in core else ''} |")
    other = other_os_packages()
    missing = [n for n in missing if not any(n == name for pkgs in other.values() for name, _ in pkgs)]
    if missing:
        lines += ["", "Not installed where this list was generated: " + ", ".join(missing) + "."]
    for label, pkgs in sorted(other.items()):
        lines += ["", f"### Only on {label}", "",
                  "| Package | Version | License | Project | In requirements.txt |", "|---|---|---|---|---|"]
        for name, version in sorted(pkgs):
            lic, url = known_license(name)
            lines.append(f"| {name} | {version} | {cell(lic)} | {url} | {'yes' if name in core else ''} |")
    lines += [
        "",
        "## Optional add-ons (not part of Jarvis)",
        "",
        "Installed only when the user chooses them, from their own sources, and never shipped with Jarvis:",
        "",
        "| Add-on | License | Installed when |",
        "|---|---|---|",
        "| kokoro-onnx (Kokoro voice engine) with phonemizer, espeakng-loader (espeak-ng), joblib, cloudpickle, dlinfo | "
        "MIT (kokoro-onnx), GPL-3.0-or-later (phonemizer, espeak-ng), BSD-3-Clause (joblib, cloudpickle), MIT (dlinfo) | "
        "a Kokoro voice is chosen (the wizard's recommendation); from PyPI into `models/addons/kokoro` |",
        "| piper-tts (Piper voice engine) and pathvalidate | GPL-3.0-or-later (piper-tts), MIT (pathvalidate) | a Piper voice is chosen; from PyPI into `models/addons/piper` |",
        "| chatterbox-tts and PyTorch | MIT (chatterbox-tts), BSD-3-Clause (PyTorch) | `scripts/setup_chatterbox.py`; into its own environment in the models folder |",
        "",
        "## Models and voices (downloaded on request)",
        "",
        "| Model | License |",
        "|---|---|",
        "| Whisper speech recognition models (CTranslate2 conversions) | MIT |",
        "| Silero VAD | MIT |",
        "| Kokoro-82M voice model and voices (ONNX export from kokoro-onnx) | Apache-2.0 |",
        "| Piper voices | per voice, see each voice's model card (linked in Settings › Models) |",
        "",
        "## Bundled in the Windows zip",
        "",
        "The zip (packaging/build_windows.py) also carries its own Python, so Jarvis needs nothing else installed:",
        "",
        "| Component | License |",
        "|---|---|",
        f"| Python {runtime['python']['version']} (python.org's embeddable package) | PSF License 2.0; the libraries "
        "it includes are listed in `runtime\\LICENSE.txt` |",
        f"| pip {runtime['pip']['version']} | MIT |",
        "",
        "## Bundled in the Mac app",
        "",
        "Jarvis.app (packaging/build_macos.py) carries its own Python too:",
        "",
        "| Component | License |",
        "|---|---|",
        f"| Python {runtime['macos_python']['version']} (python-build-standalone {runtime['macos_python']['build']}) | "
        "PSF License 2.0, see `Contents/Resources/runtime/lib/python3.13/LICENSE.txt`; statically linked: OpenSSL "
        "(Apache-2.0), SQLite (public domain), libffi (MIT), XZ Utils (0BSD), zlib (zlib), bzip2 (bzip2), mpdecimal "
        "(BSD-2-Clause), expat (MIT), libedit (BSD-3-Clause), ncurses (X11) |",
        f"| pip {runtime['pip']['version']} | MIT |",
        "",
        "## Bundled fonts",
        "",
        "| Font | License |",
        "|---|---|",
        "| Barlow and Barlow Condensed (src/gui/web/fonts) | SIL Open Font License 1.1, see `src/gui/web/fonts/OFL.txt` |",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        text = OUT.read_text(encoding="utf-8").lower() if OUT.exists() else ""
        missing = [n for n in top_level(ROOT / "requirements.txt") if f"| {n} |" not in text]
        if missing:
            print("Missing from THIRD_PARTY_LICENSES.md: " + ", ".join(missing))
            return 1
        print("THIRD_PARTY_LICENSES.md lists every core requirement.")
        return 0
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
