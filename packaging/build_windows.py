"""Build the Windows download: dist/Jarvis-<version>-windows-x64.zip, its SHA-256 and latest.json.

    python packaging/build_windows.py                          # build/windows/Jarvis, then the zip in dist/
    python packaging/build_windows.py --base-url URL           # latest.json points at URL/<zip name>
    python packaging/build_windows.py --reuse-runtime          # keep build/windows/Jarvis/runtime (faster)

Needs Windows, Python 3.11+ to run this script, internet (python.org and PyPI) and the Visual Studio C++ build tools
(cl and rc, found with vswhere) for the two small programs. What goes in the zip:

    Jarvis/
        Jarvis.exe            packaging/windows/launcher.c: starts runtime/pythonw.exe app/jarvis_gui.pyw
        Jarvis-Updater.exe    packaging/windows/updater.c: swaps in a downloaded update after Jarvis quits
        runtime/              python.org's embeddable CPython (packaging/runtime.json, checked by SHA-256), with
                              site switched on and app/ on its path, pip (add-ons are pip-installed at run time),
                              and requirements.lock installed with its hashes
        app/                  the code and the shipped files Jarvis reads at run time (APP_FILES), nothing else
        LICENSE  NOTICE  THIRD_PARTY_LICENSES.md  READ ME FIRST.txt

Before zipping: the updater must pass its tests (tests/test_packaging.py), the runtime must import every core
package, Qt WebEngine must render a page, and the app must report its version. Nothing is signed (Jarvis has no
signing budget); the release page publishes the SHA-256 instead.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"
WORK = ROOT / "build" / "windows"
STAGE = WORK / "Jarvis"
CACHE = ROOT / "build" / "cache"
DIST = ROOT / "dist"

# Tracked files that the app reads at run time (fnmatch patterns against git's paths).
APP_FILES = ["jarvis_gui.pyw", "src/*", "config/*.json", "tools_ext/README.md", "docs/GETTING_STARTED.md",
             "docs/images/*"]
TOP_FILES = ["LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md"]
# Imported by the check before zipping: one module per core requirement, plus the parts of Qt the app uses.
CORE_IMPORTS = ["sounddevice", "numpy", "faster_whisper", "ctranslate2", "onnxruntime", "claude_agent_sdk",
                "anthropic", "openai", "jsonschema", "platformdirs", "PySide6.QtWidgets", "PySide6.QtWebEngineWidgets",
                "PySide6.QtWebChannel", "win32com.client", "uiautomation", "PIL", "pip"]
# Qt WebEngine (the app's window) loads a page and runs JavaScript, offscreen: catches a pruned DLL or resource.
WEB_PROBE = """
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication
app = QApplication([])
view, result = QWebEngineView(), {}
view.page().setWebChannel(QWebChannel(view.page()))
def done(value):
    result["js"] = value
    app.quit()
def loaded(ok):
    result["loaded"] = ok
    view.page().runJavaScript("document.title + ' ' + typeof qt.webChannelTransport", 0, done)
view.loadFinished.connect(loaded)
view.setHtml("<html><head><title>web</title></head><body>Jarvis</body></html>")
QTimer.singleShot(60000, app.quit)
app.exec()
if result != {"loaded": True, "js": "web object"}:
    sys.exit(f"Qt WebEngine check failed: {result}")
print("Qt WebEngine renders pages")
"""
READ_ME_FIRST = """Jarvis {version}

1. If you haven't yet, right-click the zip, choose Extract All, then Extract.
2. Open the extracted Jarvis folder and double-click Jarvis.exe.
3. Windows may say "Windows protected your PC", because Jarvis isn't signed. Click More info, then Run anyway.

The setup guide is app\\docs\\GETTING_STARTED.md in this folder.
"""
RC_TEMPLATE = """#include <winver.h>
1 ICON "{icon}"
VS_VERSION_INFO VERSIONINFO
FILEVERSION {numbers}
PRODUCTVERSION {numbers}
FILEOS VOS_NT_WINDOWS32
FILETYPE VFT_APP
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "040904b0"
        BEGIN
            VALUE "CompanyName", "The Jarvis authors"
            VALUE "FileDescription", "{description}"
            VALUE "FileVersion", "{version}"
            VALUE "InternalName", "{name}"
            VALUE "LegalCopyright", "Apache License 2.0"
            VALUE "OriginalFilename", "{name}"
            VALUE "ProductName", "Jarvis"
            VALUE "ProductVersion", "{version}"
        END
    END
    BLOCK "VarFileInfo"
    BEGIN
        VALUE "Translation", 0x409, 1200
    END
END
"""


def log(text: str) -> None:
    print(f"[build] {text}", flush=True)


def app_version() -> str:
    text = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def version_numbers(version: str) -> str:
    numbers = [int(n) for n in re.findall(r"\d+", version.split("-")[0].split(".dev")[0])][:3]
    return ",".join(str(n) for n in numbers + [0] * (4 - len(numbers)))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(spec: dict) -> Path:
    """A pinned download from runtime.json, kept in build/cache and checked against its SHA-256."""
    dest = CACHE / spec["url"].rsplit("/", 1)[1]
    if not (dest.exists() and sha256_of(dest) == spec["sha256"]):
        CACHE.mkdir(parents=True, exist_ok=True)
        log(f"downloading {spec['url']}")
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "Jarvis build"})
        with urllib.request.urlopen(request, timeout=120) as r, open(dest, "wb") as out:
            shutil.copyfileobj(r, out)
    digest = sha256_of(dest)
    if digest != spec["sha256"]:
        dest.unlink()
        sys.exit(f"{dest.name}: SHA-256 {digest} doesn't match runtime.json")
    return dest


def run(cmd: list, **kw) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if result.returncode != 0:
        sys.exit(f"failed ({result.returncode}): {' '.join(map(str, cmd))}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def build_runtime(pins: dict) -> None:
    runtime = STAGE / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(fetch(pins["python"])) as z:
        z.extractall(runtime)
    pth = next(runtime.glob("python3*._pth"))
    stdlib = pth.stem + ".zip"                                         # python313.zip
    # The ._pth file is the whole module search path: the standard library, the runtime, its site-packages and
    # the app. PYTHONPATH and the user's own site-packages are ignored, so nothing on the PC leaks in.
    pth.write_text(f"{stdlib}\n.\nLib\\site-packages\n..\\app\nimport site\n", encoding="utf-8")
    python = runtime / "python.exe"
    log("installing pip into the runtime")
    with zipfile.ZipFile(fetch(pins["pip"])) as z:                    # a pure-Python wheel is a zip of site-packages
        z.extractall(runtime / "Lib" / "site-packages")
    log("installing requirements.lock into the runtime")
    run([str(python), "-m", "pip", "install", "--no-deps", "--require-hashes", "--only-binary=:all:",
         "--no-warn-script-location", "--disable-pip-version-check", "-r", str(ROOT / "requirements.lock")])
    shutil.rmtree(runtime / "Scripts", ignore_errors=True)            # pip.exe and friends: the app runs -m pip


def pe_imports(path: Path) -> set:
    """The DLL names a Windows program or library imports (normal and delay-loaded), from its PE headers."""
    data = path.read_bytes()
    pe = int.from_bytes(data[0x3C:0x40], "little")
    sections_count = int.from_bytes(data[pe + 6:pe + 8], "little")
    optional = pe + 24
    optional_size = int.from_bytes(data[pe + 20:pe + 22], "little")
    directories = optional + (112 if data[optional:optional + 2] == b"\x0b\x02" else 96)
    sections = []
    for i in range(sections_count):
        s = optional + optional_size + 40 * i
        virtual_size, address, raw_size, raw = (int.from_bytes(data[s + o:s + o + 4], "little") for o in (8, 12, 16, 20))
        sections.append((address, max(virtual_size, raw_size), raw))

    def offset(rva: int) -> int:
        for address, size, raw in sections:
            if address <= rva < address + size:
                return rva - address + raw
        return -1

    def name_at(rva: int) -> str:
        o = offset(rva)
        return data[o:data.index(b"\0", o)].decode("ascii", "replace").lower() if o >= 0 else ""

    names = set()
    for index, size, name_field in ((1, 20, 12), (13, 32, 4)):          # import table, delay-load import table
        rva = int.from_bytes(data[directories + 8 * index:directories + 8 * index + 4], "little")
        o = offset(rva) if rva else -1
        while o >= 0 and any(data[o:o + size]):
            names.add(name_at(int.from_bytes(data[o + name_field:o + name_field + 4], "little")))
            o += size
    return names - {""}


# What the app uses from Qt (CORE_IMPORTS' PySide6 modules), and what Qt loads at run time without importing it.
QT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets"]
QT_PLUGINS = ["platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation",
              "platforminputcontexts", "generic"]
QT_KEEP = ["QtWebEngineProcess.exe", "opengl32sw.dll", "d3dcompiler_47.dll", "msvcp*.dll", "vcruntime*.dll",
           "concrt*.dll", "pyside6.abi3.dll", "*.py", "*.json"]


def prune(runtime: Path) -> int:
    """Remove what Jarvis never loads: most of Qt (kept: the DLLs the app's Qt modules import, followed through
    their import tables), Qt's tools, debug resources, translations other than WebEngine's English, type stubs,
    and the copy of Claude Code inside the Agent SDK (the wizard installs Claude Code from Anthropic instead)."""
    site = runtime / "Lib" / "site-packages"
    qt = site / "PySide6"
    before = folder_size(runtime)
    for name in ("metatypes", "qml", "include", "typesystems", "glue", "scripts", "doc", "examples"):
        shutil.rmtree(qt / name, ignore_errors=True)
    for folder in (qt / "plugins").iterdir() if (qt / "plugins").is_dir() else []:
        if folder.name not in QT_PLUGINS:
            shutil.rmtree(folder)
    for f in (qt / "translations").rglob("*") if (qt / "translations").is_dir() else []:
        if f.is_file() and f.name != "en-US.pak":
            f.unlink()
    for f in (qt / "resources").glob("*.debug.*"):
        f.unlink()
    needed, todo = set(), [qt / f"{m}.pyd" for m in QT_MODULES] + [qt / "QtWebEngineProcess.exe"] + \
        [p for p in (qt / "plugins").rglob("*.dll")]
    local = {p.name.lower(): p for p in qt.glob("*.dll")}
    while todo:
        for name in pe_imports(todo.pop()):
            if name in local and name not in needed:
                needed.add(name)
                todo.append(local[name])
    keep_modules = {m.lower() for m in QT_MODULES} | {"qt" + n[len("qt6"):-len(".dll")] for n in needed
                                                      if n.startswith("qt6")}
    for f in qt.iterdir():
        if f.is_dir():
            continue
        name = f.name.lower()
        if any(fnmatch.fnmatch(name, k.lower()) for k in QT_KEEP) or name in needed:
            continue
        if f.suffix == ".pyd" and f.stem.lower() in keep_modules:
            continue
        f.unlink()
    for f in site.rglob("*.pyi"):
        f.unlink()
    shutil.rmtree(site / "claude_agent_sdk" / "_bundled", ignore_errors=True)
    return before - folder_size(runtime)


def tracked_files() -> list:
    out = run(["git", "ls-files", "-z"], cwd=ROOT)
    return [p for p in out.split("\0") if p]


def copy_app() -> int:
    app = STAGE / "app"
    shutil.rmtree(app, ignore_errors=True)
    files = [p for p in tracked_files() if any(fnmatch.fnmatch(p, pattern) for pattern in APP_FILES)]
    for rel in files:
        dest = app / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
    return len(files)


def vcvars() -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        sys.exit("The Visual Studio C++ build tools are needed (vswhere.exe not found).")
    found = run([str(vswhere), "-latest", "-products", "*", "-requires",
                 "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-find", r"VC\Auxiliary\Build\vcvars64.bat"])
    if not found.strip():
        sys.exit("The Visual Studio C++ build tools are needed (vcvars64.bat not found).")
    return Path(found.strip().splitlines()[0])


def compile_programs(version: str) -> None:
    """Jarvis.exe and Jarvis-Updater.exe: static C runtime, GUI subsystem, icon, version and manifest."""
    out = WORK / "programs"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    icon = str(ROOT / "src" / "gui" / "assets" / "jarvis.ico").replace("\\", "\\\\")
    steps = [f'call "{vcvars()}" >nul']
    for source, name, description in (("launcher.c", "Jarvis.exe", "Jarvis"),
                                      ("updater.c", "Jarvis-Updater.exe", "Jarvis updater")):
        stem = Path(name).stem
        (out / f"{stem}.rc").write_text(RC_TEMPLATE.format(icon=icon, numbers=version_numbers(version),
                                                           description=description, version=version, name=name),
                                        encoding="utf-8")
        steps.append(f'rc /nologo /fo "{stem}.res" "{stem}.rc"')
        steps.append(f'cl /nologo /O1 /MT /W4 /DUNICODE /D_UNICODE "{WINDOWS / source}" "{stem}.res" /Fe"{name}" '
                     f'/link /SUBSYSTEM:WINDOWS /MANIFEST:EMBED /MANIFESTINPUT:"{WINDOWS / "app.manifest"}" '
                     f'user32.lib shell32.lib')
    script = out / "compile.cmd"
    script.write_text("@echo off\r\n" + "\r\n".join(f"{s} || exit /b 1" for s in steps) + "\r\n", encoding="utf-8")
    log("compiling Jarvis.exe and Jarvis-Updater.exe")
    run(["cmd", "/d", "/c", str(script)], cwd=out)
    for name in ("Jarvis.exe", "Jarvis-Updater.exe"):
        shutil.copy2(out / name, STAGE / name)


def top_files(version: str) -> None:
    for name in TOP_FILES:
        shutil.copy2(ROOT / name, STAGE / name)
    (STAGE / "READ ME FIRST.txt").write_text(READ_ME_FIRST.format(version=version).replace("\n", "\r\n"),
                                             encoding="utf-8", newline="")


def check(version: str) -> None:
    """The runtime imports every core package from its own folders, pip works, and the app knows its version."""
    python = STAGE / "runtime" / "python.exe"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "JARVIS"))}
    probe = ("import sys, importlib\n"
             f"for name in {CORE_IMPORTS!r}:\n"
             "    importlib.import_module(name)\n"
             "outside = [p for p in sys.path if not p.lower().startswith(sys.prefix.lower().rsplit('\\\\', 1)[0])]\n"
             "assert not outside, outside\n"
             "print('imports ok')\n")
    log(run([str(python), "-c", probe], env=env, cwd=WORK).strip())
    run([str(python), "-m", "pip", "--version"], env=env, cwd=WORK)
    web = dict(env, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
    log(run([str(python), "-c", WEB_PROBE], env=web, cwd=WORK, timeout=120).strip())
    reported = run([str(python), str(STAGE / "app" / "jarvis_gui.pyw"), "--version"], env=env, cwd=WORK).strip()
    if reported != version:
        sys.exit(f"the app reports version {reported!r}, expected {version!r}")
    log(f"the app reports {reported}")


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def make_zip(version: str) -> Path:
    DIST.mkdir(exist_ok=True)
    target = DIST / f"Jarvis-{version}-windows-x64.zip"
    stamp = int(run(["git", "log", "-1", "--format=%ct"], cwd=ROOT).strip() or 0)
    date_time = __import__("time").gmtime(max(stamp, 315532800))[:6]     # the commit's time: same input, same zip
    files = sorted(p for p in STAGE.rglob("*") if p.is_file())
    log(f"zipping {len(files)} files ({folder_size(STAGE) / 1e6:.0f} MB)")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in files:
            info = zipfile.ZipInfo("Jarvis/" + path.relative_to(STAGE).as_posix(), date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(path, "rb") as f:
                z.writestr(info, f.read(), compresslevel=9)
    return target


def write_release_files(target: Path, version: str, base_url: str, notes: str) -> dict:
    digest, size = sha256_of(target), target.stat().st_size
    (target.parent / (target.name + ".sha256")).write_text(f"{digest} *{target.name}\n", encoding="utf-8")
    url = f"{base_url.rstrip('/')}/{target.name}" if base_url else target.name
    latest = {"version": version, "notes": notes, "windows": {"url": url, "sha256": digest, "size": size}}
    (target.parent / "latest.json").write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    return latest


def main(argv=None) -> int:
    if sys.platform != "win32":
        sys.exit("The Windows zip is built on Windows.")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="", help="where the zip will be downloaded from (for latest.json)")
    ap.add_argument("--notes", default="", help="release notes address or text for latest.json")
    ap.add_argument("--reuse-runtime", action="store_true", help="keep an existing build/windows/Jarvis/runtime")
    args = ap.parse_args(argv)

    version = app_version()
    pins = json.loads((ROOT / "packaging" / "runtime.json").read_text(encoding="utf-8"))
    log(f"Jarvis {version}, Python {pins['python']['version']}, pip {pins['pip']['version']}")
    if not (args.reuse_runtime and (STAGE / "runtime" / "python.exe").exists()):
        build_runtime(pins)
    log(f"pruned {prune(STAGE / 'runtime') / 1e6:.0f} MB that Jarvis never loads")
    for item in STAGE.iterdir() if STAGE.exists() else []:
        if item.name != "runtime":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    log(f"copied {copy_app()} app files")
    compile_programs(version)
    log("testing the updater on a fake Jarvis folder")
    run([sys.executable, "-m", "unittest", "tests.test_packaging"], cwd=ROOT)
    top_files(version)
    check(version)
    target = make_zip(version)
    latest = write_release_files(target, version, args.base_url, args.notes)
    log(f"{target} ({latest['windows']['size'] / 1e6:.0f} MB, SHA-256 {latest['windows']['sha256']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
