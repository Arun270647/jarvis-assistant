"""Build the Mac download: dist/Jarvis-<version>-macos-arm64.dmg, its SHA-256, and the "macos" entry of latest.json.

    python packaging/build_macos.py                          # build/macos/Jarvis.app, then the disk image in dist/
    python packaging/build_macos.py --base-url URL           # latest.json points at URL/<dmg name>
    python packaging/build_macos.py --reuse-runtime          # keep build/macos/Jarvis.app's runtime (faster)

Needs a Mac with Apple silicon, Python 3.11+ to run this script, internet (GitHub and PyPI) and the Xcode command line
tools (cc, install_name_tool, codesign). Apple silicon only: onnxruntime has no wheel for Intel Macs. The disk image
holds Jarvis.app and a link to Applications (open it, drag Jarvis onto Applications); the updater reads the same
file. What goes in the app:

    Jarvis.app/Contents/
        Info.plist            name, version, bundle id, the texts macOS shows when it asks for the microphone and
                              for permission to control Terminal and System Events
        MacOS/Jarvis          packaging/macos/launcher.c: runs Resources/app/jarvis_gui.pyw on the bundled Python
                              inside its own process, so macOS treats Jarvis.app as the app (permissions, Dock icon)
        Resources/runtime/    python-build-standalone's CPython (packaging/runtime.json, checked by SHA-256), pip,
                              and requirements.lock installed with its hashes; pruned (prune(), slim()), the
                              standard library compiled only, and every binary thinned to arm64 (thin())
        Resources/app/        the code and the shipped files Jarvis reads at run time, compiled to .pyc
        Resources/jarvis.icns, LICENSE, NOTICE, THIRD_PARTY_LICENSES.md

The pages render in the system's WebKit (src/gui/webkit_view.py), so Qt WebEngine (Chromium) isn't in the app.

Before the disk image: the runtime must import every core package, pages must render in the app's WebKit views with
the bridge answering and the Edit menu in place (WEBKIT_PROBE), the app must report its version through the real
launcher, and the signature must verify. After it: the image must mount and hold a signed app that reports its
version, and the updater's own code must copy the app out of it intact. The image is LZMA-compressed (ULMO). The app
is signed ad hoc (codesign --sign -), which Apple silicon needs to run it at all; it isn't notarized (no Apple
Developer Program), so the first start needs System Settings › Privacy & Security › Open Anyway. No hardened
runtime: only notarization requires it.
"""
import argparse
import json
import platform
import plistlib
import re
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_windows import (APP_FILES, CACHE, DIST, ROOT, TOP_FILES, app_version, fetch,  # noqa: E402
                           folder_size, log, run, sha256_of, slim, tracked_files)

WORK = ROOT / "build" / "macos"
APP = WORK / "Jarvis.app"
CONTENTS = APP / "Contents"
RESOURCES = CONTENTS / "Resources"
MACOS = ROOT / "packaging" / "macos"
BUNDLE_ID = "jarvis.assistant.desktop"          # src/platform/macos.py (the LaunchAgent's name)
MIN_MACOS = "14.0"                              # numpy and onnxruntime ship macOS 14 wheels (Qt alone would do 13)
DMG_FORMAT = "ULMO"                             # LZMA (macOS 10.15+): the smallest download hdiutil can make
ARCH = "arm64"                                  # PySide6's wheels are universal (Intel too): thinned to this
CORE_IMPORTS = ["sounddevice", "numpy", "faster_whisper", "ctranslate2", "onnxruntime", "claude_agent_sdk",
                "anthropic", "openai", "jsonschema", "platformdirs", "PySide6.QtWidgets",
                "PySide6.QtWebChannel", "WebKit", "objc", "AppKit", "Quartz", "ApplicationServices", "AVFoundation", "keyring",
                "PIL", "pip"]
# The Qt modules the Mac app imports: its pages render in the system's WebKit (src/gui/webkit_view.py), so Qt
# WebEngine, and the Qt Quick, QML, PDF and positioning frameworks only it needs, stay out of Jarvis.app.
QT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel"]
QT_PLUGINS = ["platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation", "generic",
              "permissions"]                    # (no platforminputcontexts: only the virtual keyboard)
# The app's WebKit views (src/gui/webkit_view.py), an opaque one as in the window and a transparent one as in the orb,
# load a page whose script calls a Python slot through the QWebChannel bridge: proves WebKit, the injected
# qwebchannel.js, the transport and Qt all work in the built app. Then the Edit menu must offer copy and paste.
WEBKIT_PROBE = """
import sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtWidgets import QApplication
app = QApplication([])
from AppKit import NSApplication
from Foundation import NSStringFromSelector
from src.gui.webkit_view import WebKitView
answers = []
class Probe(QObject):
    @Slot(str)
    def hello(self, text):
        answers.append(text)
        if len(answers) == 2:
            app.quit()
page = Path(tempfile.mkdtemp()) / "probe.html"
page.write_text("<html><head><title>webkit</title></head><body><script>new QWebChannel(qt.webChannelTransport, "
                "function (ch) { ch.objects.jarvis.hello(document.title + ' bridge'); });</script></body></html>",
                encoding="utf-8")
probes = [Probe(), Probe()]
views = [WebKitView(None, probes[0], page), WebKitView(None, probes[1], page, transparent=True)]
for view in views:
    view.resize(320, 200)
    view.show()
QTimer.singleShot(60000, app.quit)
app.exec()
if answers != ["webkit bridge"] * 2:
    sys.exit(f"WebKit check failed: {answers}")
edit = NSApplication.sharedApplication().mainMenu().itemWithTitle_("Edit")
keys = {(NSStringFromSelector(i.action()), i.keyEquivalent()) for i in edit.submenu().itemArray()} if edit else set()
if not {("copy:", "c"), ("paste:", "v"), ("selectAll:", "a")} <= keys:
    sys.exit(f"the Edit menu lacks copy, paste or select all: {sorted(keys)}")
print("WebKit renders pages, the bridge answers, and the Edit menu has copy and paste")
"""
MICROPHONE_TEXT = ("Jarvis listens when you click the orb or press its shortcut, to hear what you ask. Nothing is "
                   "recorded in the background.")
AUTOMATION_TEXT = ("Jarvis controls Terminal when you ask it to open a terminal window or type into one, and "
                   "System Events when you ask it to shut down or restart the Mac.")


def info_plist(version: str) -> dict:
    numbers = ".".join(re.findall(r"\d+", version.split("-")[0].split(".dev")[0])[:3]) or "0.0.0"
    return {
        "CFBundleName": "Jarvis", "CFBundleDisplayName": "Jarvis", "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "Jarvis", "CFBundleIconFile": "jarvis", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": numbers, "CFBundleVersion": version, "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": MIN_MACOS, "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True, "NSSupportsAutomaticGraphicsSwitching": True,
        "NSMicrophoneUsageDescription": MICROPHONE_TEXT, "NSAppleEventsUsageDescription": AUTOMATION_TEXT,
        "NSHumanReadableCopyright": "Apache License 2.0",
    }


def dmg_name(version: str, arch: str = "arm64") -> str:
    return f"Jarvis-{version}-macos-{arch}.dmg"


def merge_latest(latest_path: Path, version: str, notes: str, entry: dict) -> dict:
    """Add the "macos" entry to latest.json (the Windows build writes the rest); start one when there is none."""
    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {}
    if latest.get("version") not in (None, version):
        raise SystemExit(f"{latest_path} is for version {latest['version']}, this build is {version}")
    latest.update(version=version, notes=latest.get("notes") or notes, macos=entry)
    latest_path.write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    return latest


# ------------------------------------------------------------------ steps

def build_runtime(pins: dict) -> Path:
    runtime = RESOURCES / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True)
    log("unpacking the runtime")
    with tarfile.open(fetch(pins["macos_python"])) as tar:
        members = []
        for m in tar.getmembers():
            if not m.name.startswith("python/"):
                continue
            m.name = m.name[len("python/"):]
            if m.name:
                members.append(m)
        tar.extractall(runtime, members=members, filter="tar")
    python = runtime / "bin" / "python3"
    log("installing the pinned pip and requirements.lock into the runtime")
    run([str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", "--disable-pip-version-check",
         str(fetch(pins["pip"]))])
    run([str(python), "-m", "pip", "install", "--no-deps", "--require-hashes", "--only-binary=:all:",
         "--no-warn-script-location", "--disable-pip-version-check", "-r", str(ROOT / "requirements.lock")])
    return python


def _site(runtime: Path) -> Path:
    return next((runtime / "lib").glob("python3.*")) / "site-packages"


def macho_links(path: Path) -> list:
    """The libraries a Mach-O file loads (otool -L), without the file's own id."""
    out = run(["otool", "-L", str(path)]).splitlines()[1:]
    return [line.strip().split(" (")[0] for line in out if line.strip()]


def prune(runtime: Path) -> int:
    """Remove what Jarvis never loads: Qt frameworks and loose libraries the app's Qt modules don't link (followed
    through otool; the loose ones are FFmpeg for Qt Multimedia, and Qt WebEngine goes this way too), the Python
    bindings of Qt modules the app doesn't import, QML, Qt's translations (Jarvis is in English), Qt's tools, type
    stubs, and the Agent SDK's copy of Claude Code (the wizard installs Claude Code from Anthropic instead)."""
    site = _site(runtime)
    qt, before = site / "PySide6", folder_size(runtime)
    lib = qt / "Qt" / "lib"
    for name in ("Qt/qml", "Qt/metatypes", "Qt/translations", "include", "typesystems", "glue", "scripts", "doc",
                 "examples", "Assistant.app", "Designer.app", "Linguist.app"):
        shutil.rmtree(qt / name, ignore_errors=True)
    plugins = qt / "Qt" / "plugins"
    for folder in plugins.iterdir() if plugins.is_dir() else []:
        if folder.name not in QT_PLUGINS:
            shutil.rmtree(folder)
    frameworks = {p.name[:-len(".framework")]: p for p in lib.glob("*.framework")}
    dylibs = {p.name: p for p in lib.glob("*.dylib")}
    for f in plugins.glob("imageformats/libqpdf*"):    # PDFs as images: the only thing that would need Qt PDF
        f.unlink()
    bindings = set(QT_MODULES)
    todo = [qt / f"{m}.abi3.so" for m in bindings] + list(plugins.rglob("*.dylib"))
    needed, needed_dylibs = set(), set()
    while todo:
        for link in macho_links(todo.pop()):
            m = re.search(r"([^/]+)\.framework/", link)
            if m and m.group(1) in frameworks and m.group(1) not in needed:
                needed.add(m.group(1))
                binary = next((frameworks[m.group(1)] / "Versions").glob("*/" + m.group(1)), None)
                if binary is not None:
                    todo.append(binary)
            name = link.rsplit("/", 1)[-1]
            if name in dylibs and name not in needed_dylibs:
                needed_dylibs.add(name)
                todo.append(dylibs[name])
    for name, path in frameworks.items():
        if name not in needed:
            shutil.rmtree(path)
    for name, path in dylibs.items():
        if name not in needed_dylibs:
            path.unlink()
    link_framework_copies(lib)
    for f in qt.iterdir():                  # bindings the app doesn't import, Qt's tools (qmlls, lupdate…)
        if f.is_file() and ((f.name.endswith(".abi3.so") and f.name.split(".")[0] not in bindings) or
                            (not f.suffix and os.access(f, os.X_OK))):
            f.unlink()
    linked = {link.rsplit("/", 1)[-1] for m in bindings for link in macho_links(qt / f"{m}.abi3.so")}
    for f in qt.glob("libpyside6*.dylib"):  # libpyside6qml serves only the QML bindings
        if f.name not in linked:
            f.unlink()
    for f in site.rglob("*.pyi"):
        f.unlink()
    shutil.rmtree(site / "claude_agent_sdk" / "_bundled", ignore_errors=True)
    for f in (runtime / "bin").iterdir():                      # the app runs `python3 -m pip`; idle and friends go
        if f.name.startswith(("idle", "pydoc", "pip")) or f.name.endswith("-config"):
            f.unlink()
    stdlib = next((runtime / "lib").glob("python3.*"))
    for name in ("test", "idlelib", "tkinter", "turtledemo", "ensurepip"):     # Jarvis has no Tk windows
        shutil.rmtree(stdlib / name, ignore_errors=True)
    tk = list((runtime / "lib").glob("libt[ck]*")) + list((stdlib / "lib-dynload").glob("_tkinter*"))
    for f in tk:
        f.unlink()
    for folder in list((runtime / "lib").glob("tcl*")) + list((runtime / "lib").glob("tk*")) + [runtime / "share"]:
        shutil.rmtree(folder, ignore_errors=True)
    return before - folder_size(runtime)


def copy_app() -> int:
    app = RESOURCES / "app"
    shutil.rmtree(app, ignore_errors=True)
    import fnmatch
    files = [p for p in tracked_files() if any(fnmatch.fnmatch(p, pattern) for pattern in APP_FILES)]
    for rel in files:
        dest = app / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
    return len(files)


def compile_launcher(runtime: Path) -> None:
    """MacOS/Jarvis, linked against the bundled libpython and pointed at it relative to itself."""
    out = CONTENTS / "MacOS" / "Jarvis"
    out.parent.mkdir(parents=True, exist_ok=True)
    include = next((runtime / "include").glob("python3.*"))
    libname = include.name                                      # python3.13
    log("compiling the launcher")
    run(["cc", "-O2", "-Wall", "-Wextra", f"-mmacosx-version-min={MIN_MACOS}", "-arch", "arm64", f"-I{include}",
         str(MACOS / "launcher.c"), f"-L{runtime / 'lib'}", f"-l{libname}", "-o", str(out)])
    dylib = runtime / "lib" / f"lib{libname}.dylib"
    old = next(link for link in macho_links(out) if f"lib{libname}.dylib" in link)
    run(["install_name_tool", "-change", old, f"@executable_path/../Resources/runtime/lib/{dylib.name}", str(out)])


def compile_python_command(runtime: Path) -> None:
    """bin/python3.13 from packaging/macos/python.c: a small program on the bundled libpython, in place of the
    standalone build's second, statically linked copy of the interpreter (18 MB)."""
    include = next((runtime / "include").glob("python3.*"))
    libname = include.name                                      # python3.13
    out = runtime / "bin" / libname
    log("compiling the python3 command")
    run(["cc", "-O2", "-Wall", "-Wextra", f"-mmacosx-version-min={MIN_MACOS}", "-arch", "arm64", f"-I{include}",
         str(MACOS / "python.c"), f"-L{runtime / 'lib'}", f"-l{libname}", "-o", str(out) + ".new"])
    old = next(link for link in macho_links(Path(str(out) + ".new")) if f"lib{libname}.dylib" in link)
    run(["install_name_tool", "-change", old, f"@executable_path/../lib/lib{libname}.dylib", str(out) + ".new"])
    Path(str(out) + ".new").replace(out)


def link_framework_copies(lib: Path) -> None:
    """Wheels can't hold symlinks, so each Qt framework's top-level Resources (and Helpers) is a second copy of
    Versions/A's. Put the standard layout back: Versions/Current -> A, and each top-level copy replaced by a link
    to Versions/Current/<name>, plus the framework's own binary link. Both paths then reach the same files."""
    for framework in lib.glob("*.framework"):
        versions = framework / "Versions"
        a = versions / "A"
        if not a.is_dir():
            continue
        if not (versions / "Current").exists():
            (versions / "Current").symlink_to("A")
        for item in list(framework.iterdir()):
            twin = a / item.name
            if item.name == "Versions" or item.is_symlink() or not twin.exists():
                continue
            if item.is_dir():
                mine = sorted((p.relative_to(item).as_posix(), p.stat().st_size) for p in item.rglob("*") if p.is_file())
                theirs = sorted((p.relative_to(twin).as_posix(), p.stat().st_size) for p in twin.rglob("*") if p.is_file())
                if mine != theirs:
                    continue
                shutil.rmtree(item)
            else:
                item.unlink()
            item.symlink_to(Path("Versions") / "Current" / item.name)
        binary = framework / framework.stem
        if (a / framework.stem).is_file() and not binary.exists():
            binary.symlink_to(Path("Versions") / "Current" / framework.stem)


def macho_files() -> list:
    found = []
    for path in APP.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with open(path, "rb") as f:
            if f.read(4) == b"\xcf\xfa\xed\xfe":
                found.append(path)
    return found


def strip_binaries() -> int:
    """Local symbols out of every Mach-O file (strip -x keeps the global ones other libraries and Python link to);
    ONNX Runtime alone carries 27 MB of them. Done before signing, which strip would undo. The .dSYM debug symbols
    some wheels ship (PyObjC) go too."""
    before = folder_size(APP)
    for dsym in [p for p in APP.rglob("*.dSYM") if p.is_dir()]:
        shutil.rmtree(dsym)
    for path in macho_files():
        subprocess.run(["strip", "-x", str(path)], capture_output=True)     # a file strip can't handle stays as is
    return before - folder_size(APP)


def write_bundle_files(version: str) -> None:
    (CONTENTS / "Info.plist").write_bytes(plistlib.dumps(info_plist(version)))
    (CONTENTS / "PkgInfo").write_text("APPL????", encoding="ascii")
    sys.path.insert(0, str(ROOT / "scripts"))
    from make_logo import render
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    frames = [render(s, detailed=s >= 48) for s in sizes]
    frames[-1].save(RESOURCES / "jarvis.icns", format="ICNS", append_images=frames[:-1])
    for name in TOP_FILES:
        shutil.copy2(ROOT / name, RESOURCES / name)


def compile_bytecode(python: Path) -> None:
    """The app writes no .pyc at run time (it would change the signed app), so compile everything now. The standard
    library ships compiled only, like the Windows runtime's (site-packages already is, from slim()); the app keeps its
    sources next to its __pycache__, for readable tracebacks."""
    log("compiling the Python files")
    stdlib = next((RESOURCES / "runtime" / "lib").glob("python3.*"))
    run([str(python), "-m", "compileall", "-b", "-q", "-j0", "-x", r"[\\/]site-packages[\\/]", str(stdlib)])
    in_stdlib = [p for p in stdlib.rglob("*") if "site-packages" not in p.relative_to(stdlib).parts]
    for f in in_stdlib:
        if f.suffix == ".py" and f.with_suffix(".pyc").is_file():
            f.unlink()
    for cache in [p for p in in_stdlib if p.name == "__pycache__"]:
        shutil.rmtree(cache, ignore_errors=True)
    run([str(python), "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash",
         str(RESOURCES / "app"), str(_site(RESOURCES / "runtime"))])


def thin() -> int:
    """PySide6's Mac wheels are universal: every Qt library carries an Intel copy as well. The app runs on Apple
    silicon only, so ditto copies the app keeping the arm64 code alone (about half of Qt)."""
    before = folder_size(APP)
    thinned = APP.with_name(APP.name + ".thin")
    shutil.rmtree(thinned, ignore_errors=True)
    run(["ditto", "--arch", ARCH, str(APP), str(thinned)])
    shutil.rmtree(APP)
    thinned.rename(APP)
    return before - folder_size(APP)


def sign() -> None:
    """Every Mach-O file first, then the app. --deep signs only nested code in the standard places (Frameworks,
    PlugIns), not the libraries under Resources/runtime: those keep the signature their wheel came with, which strip
    leaves stale when it isn't the linker's own (ctranslate2's and Pillow's, from delocate). The app's seal still
    verifies, but macOS 27 ends the app the moment it loads such a library (0.1.0-alpha.3, when Whisper loaded)."""
    log("signing ad hoc")
    binaries = macho_files()
    for path in binaries:
        run(["codesign", "--force", "--sign", "-", str(path)])
    run(["codesign", "--force", "--deep", "--sign", "-", "--identifier", BUNDLE_ID, str(APP)])
    run(["codesign", "--verify", "--deep", "--strict", str(APP)])
    for path in binaries:
        run(["codesign", "--verify", "--strict", str(path)])
    log(f"signed and verified {len(binaries)} binaries one by one")


def check(version: str, python: Path) -> None:
    """The runtime imports every core package from its own folders, WebKit renders the pages (WEBKIT_PROBE), and the
    real launcher starts the app far enough to report its version."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "JARVIS"))}
    probe = ("import sys, importlib\n"
             f"for name in {CORE_IMPORTS!r}:\n"
             "    importlib.import_module(name)\n"
             "outside = [p for p in sys.path if p and not p.startswith(sys.prefix)]\n"
             "assert not outside, outside\n"
             "print('imports ok')\n")
    # -B: nothing may write .pyc files into the signed app.
    log(run([str(python), "-I", "-B", "-c", probe], env=env, cwd=WORK).strip())
    run([str(python), "-I", "-B", "-m", "pip", "--version"], env=env, cwd=WORK)
    log(run([str(python), "-I", "-B", "-c", WEBKIT_PROBE, str(RESOURCES / "app")], env=env, cwd=WORK,
            timeout=180).strip())
    reported = run([str(CONTENTS / "MacOS" / "Jarvis"), "--version"], env=env, cwd=WORK).strip()
    if reported != version:
        sys.exit(f"the app reports version {reported!r}, expected {version!r}")
    log(f"the app reports {reported}")


def hdiutil(args: list, attempts: int = 5) -> str:
    """hdiutil, tried again a few times: on GitHub's Macs it now and then fails with "Resource busy" while the
    system still looks at a new image."""
    for attempt in range(1, attempts + 1):
        result = subprocess.run(["hdiutil", *args], capture_output=True, text=True, timeout=1200)
        if result.returncode == 0:
            return result.stdout
        log(f"hdiutil {args[0]} failed (attempt {attempt} of {attempts}): {result.stderr.strip()[-500:]}")
        time.sleep(5 * attempt)
    sys.exit(f"failed: hdiutil {' '.join(map(str, args))}")


def make_dmg(version: str) -> Path:
    """A compressed, read-only disk image with Jarvis.app and a link to Applications."""
    DIST.mkdir(exist_ok=True)
    target = DIST / dmg_name(version)
    target.unlink(missing_ok=True)
    run(["codesign", "--verify", "--deep", "--strict", str(APP)])         # the checks changed nothing in the app
    folder = WORK / "dmg"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    run(["ditto", str(APP), str(folder / APP.name)])
    (folder / "Applications").symlink_to("/Applications")
    log(f"making the disk image ({folder_size(APP) / 1e6:.0f} MB app)")
    hdiutil(["create", "-volname", "Jarvis", "-srcfolder", str(folder), "-fs", "HFS+", "-format", DMG_FORMAT,
             "-ov", str(target)])
    hdiutil(["verify", str(target)])
    shutil.rmtree(folder)
    return target


def check_dmg(target: Path, version: str) -> None:
    """The image as people get it: it mounts and holds a signed Jarvis.app that reports this version and a link to
    Applications. Then src/app/updates.py's own stage() copies the app out of it, as an update would."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "JARVIS"))}
    mount = WORK / "mounted"
    mount.mkdir(exist_ok=True)
    hdiutil(["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(target)])
    try:
        app = mount / APP.name
        run(["codesign", "--verify", "--deep", "--strict", str(app)])
        if os.readlink(mount / "Applications") != "/Applications":
            sys.exit("the disk image's Applications link is wrong")
        reported = run([str(app / "Contents" / "MacOS" / "Jarvis"), "--version"], env=env, cwd=WORK).strip()
        if reported != version:
            sys.exit(f"the app in the disk image reports version {reported!r}, expected {version!r}")
    finally:
        hdiutil(["detach", str(mount)])
    home = WORK / "update-check"
    shutil.rmtree(home, ignore_errors=True)
    probe = ("from pathlib import Path\nfrom src.app import updates\n"
             f"print(updates.stage(Path({str(target)!r}), Path('/Applications/Jarvis.app')))\n")
    staged = Path(run([sys.executable, "-c", probe], env=dict(env, JARVIS_HOME=str(home)), cwd=ROOT,
                      timeout=900).strip().splitlines()[-1])
    run(["codesign", "--verify", "--deep", "--strict", str(staged)])
    shutil.rmtree(home)
    log("the disk image mounts, its app reports its version, and the updater copies it out intact")


def write_release_files(target: Path, version: str, base_url: str, notes: str) -> dict:
    digest, size = sha256_of(target), target.stat().st_size
    (target.parent / (target.name + ".sha256")).write_text(f"{digest} *{target.name}\n", encoding="utf-8")
    url = f"{base_url.rstrip('/')}/{target.name}" if base_url else target.name
    return merge_latest(target.parent / "latest.json", version, notes, {"url": url, "sha256": digest, "size": size})


def main(argv=None) -> int:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        sys.exit("The Mac app is built on a Mac with Apple silicon.")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="", help="where the disk image will be downloaded from (for latest.json)")
    ap.add_argument("--notes", default="", help="release notes address or text for latest.json")
    ap.add_argument("--reuse-runtime", action="store_true", help="keep an existing runtime in build/macos")
    args = ap.parse_args(argv)

    version = app_version()
    pins = json.loads((ROOT / "packaging" / "runtime.json").read_text(encoding="utf-8"))
    log(f"Jarvis {version}, Python {pins['macos_python']['version']} ({pins['macos_python']['build']}), "
        f"pip {pins['pip']['version']}")
    CACHE.mkdir(parents=True, exist_ok=True)
    python = RESOURCES / "runtime" / "bin" / "python3"
    if not (args.reuse_runtime and python.exists()):
        shutil.rmtree(APP, ignore_errors=True)
        python = build_runtime(pins)
    log(f"pruned {prune(RESOURCES / 'runtime') / 1e6:.0f} MB that Jarvis never loads")
    log(f"slimmed site-packages by {slim(_site(RESOURCES / 'runtime'), python) / 1e6:.0f} MB")
    log(f"copied {copy_app()} app files")
    compile_launcher(RESOURCES / "runtime")
    compile_python_command(RESOURCES / "runtime")
    write_bundle_files(version)
    compile_bytecode(python)
    log(f"thinned to {ARCH}, {thin() / 1e6:.0f} MB of Intel code left out")
    log(f"stripped {strip_binaries() / 1e6:.0f} MB of local symbols")
    sign()
    check(version, python)
    target = make_dmg(version)
    check_dmg(target, version)
    write_release_files(target, version, args.base_url, args.notes)
    log(f"done: {target} ({target.stat().st_size / 1e6:.0f} MB), SHA-256 {sha256_of(target)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired as e:
        sys.exit(f"timed out: {e}")
