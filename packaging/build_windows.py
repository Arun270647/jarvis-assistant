"""Build the Windows download: dist/Jarvis-Setup-<version>-windows-x64.exe, its SHA-256 and latest.json.

    python packaging/build_windows.py                          # build/windows/Jarvis, then the installer in dist/
    python packaging/build_windows.py --base-url URL           # latest.json points at URL/<installer name>
    python packaging/build_windows.py --reuse-runtime          # keep build/windows/Jarvis/runtime (faster)
    python packaging/build_windows.py --check-install          # also install it silently and start it (CI)

Needs Windows, Python 3.11+ with requirements.lock installed to run this script, internet (python.org and PyPI), the
Visual Studio C++ build tools (cl and rc, found with vswhere) for the launcher, and Inno Setup 6 (ISCC; found on
PATH, in INNO_SETUP or in its usual folders) for the installer. What the installer puts in
%LOCALAPPDATA%\\Programs\\Jarvis (packaging/windows/jarvis.iss):

    Jarvis.exe            packaging/windows/launcher.c: starts runtime/pythonw.exe app/jarvis_gui.pyw
    runtime/              python.org's embeddable CPython (packaging/runtime.json, checked by SHA-256), with site
                          switched on and app/ on its path, pip (add-ons are pip-installed at run time), and
                          requirements.lock installed with its hashes, slimmed (slim())
    app/                  the code and the shipped files Jarvis reads at run time (APP_FILES), nothing else
    LICENSE  NOTICE  THIRD_PARTY_LICENSES.md

Before the installer: the runtime must import every core package, Qt WebEngine must render a page, and the app must
report its version. With --check-install the installer then installs into build/windows/install-check (registered
like a real install, so it's meant for CI's throwaway machines) and the installed copy must report its version.
Nothing is signed (Jarvis has no signing budget); the release page publishes the SHA-256 instead.
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
# Only these modules' Python bindings ship, plus QT_BINDINGS_EXTRA: QtWebEngineCore's binding loads QtPrintSupport's
# (QWebEngineView.print). Other Qt libraries ship only as the libraries the kept ones link to. No
# platforminputcontexts: it only holds Qt's virtual keyboard (system input methods live in the platform plugin).
QT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets"]
QT_BINDINGS_EXTRA = ["QtPrintSupport"]
QT_PLUGINS = ["platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation", "generic"]
# Third-party files Jarvis never loads, left out of both builds (slim()). PyAV goes too: faster-whisper imports it
# only to read audio files, Jarvis hands it microphone audio, and its wheels bundle FFmpeg with GPL encoders (x264,
# x265) that an Apache-2.0 download can't carry. A stand-in (AV_STANDIN) keeps faster-whisper's import working.
DROP_PACKAGES = ["av", "av.libs", "hf_xet",             # hf_xet: an optional download accelerator for huggingface_hub
                 "PyObjCTest"]                          # PyObjC's own test suite (macOS)
DROP_FOLDERS = ["tests", "test"]                        # the packages' own test suites
ONNX_TOOLS = ["transformers", "tools", "quantization"]  # ONNX Runtime's model-conversion tooling
# ONNX Runtime's C API library: its Python module has the engine built in and loads only system libraries.
ONNX_C_API = ["onnxruntime.dll", "libonnxruntime*.dylib"]
PYWIN32_EXTRAS = ["pythonwin", "PyWin32.chm", "adodbapi", "isapi"]      # the Pythonwin editor, help, IIS and ADO parts
KEEP_SOURCE = {"pip", "PySide6", "shiboken6"}           # shipped as .py: pip installs add-ons, Qt's own helpers
AV_STANDIN = ("# Stand-in for PyAV, which Jarvis's builds leave out (packaging/build_windows.py, DROP_PACKAGES):\n"
              "# faster-whisper imports av to read audio files, and Jarvis gives it audio arrays instead.\n\n\n"
              "def __getattr__(name):\n"
              "    raise AttributeError(f\"PyAV isn't part of Jarvis (asked for av.{name})\")\n")
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
    for f in set((qt / "resources").glob("*.debug.*")) | set((qt / "resources").glob("*devtools*")):
        f.unlink()                                      # DevTools' pages: Jarvis never opens the web inspector
    needed, todo = set(), [qt / f"{m}.pyd" for m in QT_MODULES + QT_BINDINGS_EXTRA] + [qt / "QtWebEngineProcess.exe"] + \
        [p for p in (qt / "plugins").rglob("*.dll")]
    local = {p.name.lower(): p for p in qt.glob("*.dll")}
    while todo:
        for name in pe_imports(todo.pop()):
            if name in local and name not in needed:
                needed.add(name)
                todo.append(local[name])
    keep_modules = {m.lower() for m in QT_MODULES + QT_BINDINGS_EXTRA}
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
    for name in PYWIN32_EXTRAS:
        path = site / name
        shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
    return before - folder_size(runtime)


def slim(site: Path, python: Path) -> int:
    """Leave out of site-packages what Jarvis never loads (DROP_PACKAGES with a stand-in for PyAV, the packages'
    test suites, ONNX Runtime's tooling), then ship the rest compiled only: each module as a .pyc in place of its
    .py (KEEP_SOURCE excepted), without __pycache__ folders. That halves the files and leaves nothing to compile
    at the first start. Tracebacks from these packages still give file and line. Used by both builds; python is
    the runtime's own interpreter, so the .pyc files match it."""
    before = folder_size(site)
    for name in DROP_PACKAGES:
        shutil.rmtree(site / name, ignore_errors=True)
        for info in site.glob(f"{name}-*.dist-info"):
            shutil.rmtree(info)
    (site / "av").mkdir()
    (site / "av" / "__init__.py").write_text(AV_STANDIN, encoding="utf-8")
    for folder in sorted((d for d in site.rglob("*") if d.is_dir() and d.name in DROP_FOLDERS), key=lambda d: len(d.parts)):
        shutil.rmtree(folder, ignore_errors=True)
    for name in ONNX_TOOLS:
        shutil.rmtree(site / "onnxruntime" / name, ignore_errors=True)
    for pattern in ONNX_C_API:
        for f in (site / "onnxruntime" / "capi").glob(pattern):
            f.unlink()
    keep = "|".join(sorted(KEEP_SOURCE))
    run([str(python), "-m", "compileall", "-b", "-q", "-j0", "-x", rf"[\\/]({keep})[\\/]", str(site)])
    for f in site.rglob("*.py"):
        if f.relative_to(site).parts[0] not in KEEP_SOURCE and f.with_suffix(".pyc").is_file():
            f.unlink()
    for cache in [d for d in site.rglob("__pycache__") if d.relative_to(site).parts[0] not in KEEP_SOURCE]:
        shutil.rmtree(cache, ignore_errors=True)
    return before - folder_size(site)


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
    """Jarvis.exe: static C runtime, GUI subsystem, icon, version and manifest."""
    out = WORK / "programs"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    icon = str(ROOT / "src" / "gui" / "assets" / "jarvis.ico").replace("\\", "\\\\")
    steps = [f'call "{vcvars()}" >nul']
    for source, name, description in (("launcher.c", "Jarvis.exe", "Jarvis"),):
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
    log("compiling Jarvis.exe")
    run(["cmd", "/d", "/c", str(script)], cwd=out)
    shutil.copy2(out / "Jarvis.exe", STAGE / "Jarvis.exe")


def top_files() -> None:
    for name in TOP_FILES:
        shutil.copy2(ROOT / name, STAGE / name)


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
    """Bytes on disk: a symlink (the Mac frameworks' links) counts as nothing, not as what it points to."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def iscc() -> Path:
    """Inno Setup's compiler: on PATH, in the INNO_SETUP folder, or where Inno Setup 6 installs itself."""
    found = shutil.which("ISCC")
    folders = [os.environ.get("INNO_SETUP", "")] + [str(Path(os.environ.get(v, "")) / sub / "Inno Setup 6")
                                                    for v, sub in (("ProgramFiles(x86)", ""), ("ProgramFiles", ""),
                                                                   ("LOCALAPPDATA", "Programs"))]
    for candidate in [found] + [str(Path(f) / "ISCC.exe") for f in folders if f]:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    sys.exit("Inno Setup 6 is needed for the installer (ISCC.exe not found; set INNO_SETUP to its folder).")


def installer_name(version: str) -> str:
    return f"Jarvis-Setup-{version}-windows-x64"


def make_installer(version: str) -> Path:
    """Compile packaging/windows/jarvis.iss around the prepared folder: one solid LZMA2 archive in a setup program."""
    DIST.mkdir(exist_ok=True)
    target = DIST / (installer_name(version) + ".exe")
    target.unlink(missing_ok=True)
    files = sum(1 for p in STAGE.rglob("*") if p.is_file())
    log(f"compiling the installer around {files} files ({folder_size(STAGE) / 1e6:.0f} MB)")
    defines = {"AppVersion": version, "AppNumbers": version_numbers(version).replace(",", "."),
               "SourceDir": str(STAGE), "OutputDir": str(DIST), "OutputName": installer_name(version),
               "IconFile": str(ROOT / "src" / "gui" / "assets" / "jarvis.ico"),
               "SizeKB": str(folder_size(STAGE) // 1024)}
    run([str(iscc()), "/Q"] + [f"/D{k}={v}" for k, v in defines.items()] + [str(WINDOWS / "jarvis.iss")], timeout=3600)
    return target


def check_install(target: Path, version: str) -> None:
    """Install silently, the way the updater does, and start the installed copy far enough to report its version."""
    folder = WORK / "install-check"
    shutil.rmtree(folder, ignore_errors=True)
    run([str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={folder}"], timeout=900)
    for rel in ("Jarvis.exe", "runtime/pythonw.exe", "app/jarvis_gui.pyw", "LICENSE"):
        if not (folder / rel).is_file():
            sys.exit(f"the installer didn't install {rel}")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "JARVIS"))}
    reported = run([str(folder / "runtime" / "python.exe"), str(folder / "app" / "jarvis_gui.pyw"), "--version"],
                   env=env, cwd=WORK).strip()
    if reported != version:
        sys.exit(f"the installed app reports version {reported!r}, expected {version!r}")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis") as key:
        if Path(winreg.QueryValueEx(key, "InstallLocation")[0]) != folder:
            sys.exit("the Settings > Apps entry doesn't point at the installed copy")
    log(f"installed silently, the installed copy reports {reported}, and Settings > Apps lists it")


def write_release_files(target: Path, version: str, base_url: str, notes: str) -> dict:
    digest, size = sha256_of(target), target.stat().st_size
    # Unix line endings, so `sha256sum -c` reads the file on any system (on Windows, \n would become \r\n).
    (target.parent / (target.name + ".sha256")).write_text(f"{digest} *{target.name}\n", encoding="utf-8",
                                                           newline="\n")
    url = f"{base_url.rstrip('/')}/{target.name}" if base_url else target.name
    latest = {"version": version, "notes": notes, "windows": {"url": url, "sha256": digest, "size": size}}
    (target.parent / "latest.json").write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    return latest


def main(argv=None) -> int:
    if sys.platform != "win32":
        sys.exit("The Windows installer is built on Windows.")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="", help="where the installer will be downloaded from (for latest.json)")
    ap.add_argument("--notes", default="", help="release notes address or text for latest.json")
    ap.add_argument("--reuse-runtime", action="store_true", help="keep an existing build/windows/Jarvis/runtime")
    ap.add_argument("--check-install", action="store_true",
                    help="install the result silently into build/windows/install-check and start it (for CI)")
    args = ap.parse_args(argv)

    version = app_version()
    pins = json.loads((ROOT / "packaging" / "runtime.json").read_text(encoding="utf-8"))
    log(f"Jarvis {version}, Python {pins['python']['version']}, pip {pins['pip']['version']}")
    if not (args.reuse_runtime and (STAGE / "runtime" / "python.exe").exists()):
        build_runtime(pins)
    log(f"pruned {prune(STAGE / 'runtime') / 1e6:.0f} MB that Jarvis never loads")
    runtime = STAGE / "runtime"
    log(f"slimmed site-packages by {slim(runtime / 'Lib' / 'site-packages', runtime / 'python.exe') / 1e6:.0f} MB")
    for item in STAGE.iterdir() if STAGE.exists() else []:
        if item.name != "runtime":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    log(f"copied {copy_app()} app files")
    compile_programs(version)
    top_files()
    check(version)
    target = make_installer(version)
    if args.check_install:
        check_install(target, version)
    latest = write_release_files(target, version, args.base_url, args.notes)
    log(f"{target} ({latest['windows']['size'] / 1e6:.0f} MB, SHA-256 {latest['windows']['sha256']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
