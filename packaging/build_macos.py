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
                              and requirements.lock installed with its hashes
        Resources/app/        the code and the shipped files Jarvis reads at run time, compiled to .pyc
        Resources/jarvis.icns, LICENSE, NOTICE, THIRD_PARTY_LICENSES.md

Before the disk image: the runtime must import every core package, Qt WebEngine must render a page, the app must
report its version through the real launcher, and the signature must verify. After it: the image must mount and
hold a signed app that reports its version, and the updater's own code must copy the app out of it intact. The app is signed ad hoc (codesign --sign -),
which Apple silicon needs to run it at all; it isn't notarized (no Apple Developer Program), so the first start
needs System Settings › Privacy & Security › Open Anyway. No hardened runtime: only notarization requires it, and
without it Qt WebEngine's JavaScript engine needs no extra entitlements.
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
from build_windows import (APP_FILES, CACHE, DIST, ROOT, TOP_FILES, WEB_PROBE, app_version, fetch,  # noqa: E402
                           folder_size, log, run, sha256_of, tracked_files)

WORK = ROOT / "build" / "macos"
APP = WORK / "Jarvis.app"
CONTENTS = APP / "Contents"
RESOURCES = CONTENTS / "Resources"
MACOS = ROOT / "packaging" / "macos"
BUNDLE_ID = "jarvis.assistant.desktop"          # src/platform/macos.py (the LaunchAgent's name)
MIN_MACOS = "13.0"                              # Qt 6.10+ supports macOS 13 Ventura and later
DMG_FORMAT = "ULFO"                             # lzfse: about as small as zlib, and quicker to copy the app out of
CORE_IMPORTS = ["sounddevice", "numpy", "faster_whisper", "ctranslate2", "onnxruntime", "claude_agent_sdk",
                "anthropic", "openai", "jsonschema", "platformdirs", "PySide6.QtWidgets", "PySide6.QtWebEngineWidgets",
                "PySide6.QtWebChannel", "objc", "AppKit", "Quartz", "ApplicationServices", "AVFoundation", "keyring",
                "PIL", "pip"]
QT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets"]
QT_PLUGINS = ["platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation",
              "platforminputcontexts", "generic", "permissions"]
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
    """Remove what Jarvis never loads: Qt frameworks the app's Qt modules don't link (followed through otool),
    QML, translations other than WebEngine's English, Qt's tools, type stubs, and the Agent SDK's copy of Claude
    Code (the wizard installs Claude Code from Anthropic instead)."""
    site = _site(runtime)
    qt, before = site / "PySide6", folder_size(runtime)
    lib = qt / "Qt" / "lib"
    for name in ("Qt/qml", "Qt/metatypes", "include", "typesystems", "glue", "scripts", "doc", "examples",
                 "Assistant.app", "Designer.app", "Linguist.app"):
        shutil.rmtree(qt / name, ignore_errors=True)
    plugins = qt / "Qt" / "plugins"
    for folder in plugins.iterdir() if plugins.is_dir() else []:
        if folder.name not in QT_PLUGINS:
            shutil.rmtree(folder)
    for f in (qt / "Qt" / "translations").rglob("*") if (qt / "Qt" / "translations").is_dir() else []:
        if f.is_file() and f.name != "en-US.pak":
            f.unlink()
    frameworks = {p.name[:-len(".framework")]: p for p in lib.glob("*.framework")}
    process = next(lib.glob("QtWebEngineCore.framework/**/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess"),
                   None)
    todo = [qt / f"{m}.abi3.so" for m in QT_MODULES] + list(plugins.rglob("*.dylib")) + ([process] if process else [])
    needed = set()
    while todo:
        for link in macho_links(todo.pop()):
            m = re.search(r"([^/]+)\.framework/", link)
            if m and m.group(1) in frameworks and m.group(1) not in needed:
                needed.add(m.group(1))
                binary = next((frameworks[m.group(1)] / "Versions").glob("*/" + m.group(1)), None)
                if binary is not None:
                    todo.append(binary)
    for name, path in frameworks.items():
        if name not in needed:
            shutil.rmtree(path)
    keep = set(QT_MODULES) | needed
    for f in qt.glob("Qt*.abi3.so"):
        if f.name.split(".")[0] not in keep:
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
    """The app writes no .pyc at run time (it would change the signed app), so compile everything now."""
    log("compiling the Python files")
    run([str(python), "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash",
         str(RESOURCES / "app"), str(next((RESOURCES / "runtime" / "lib").glob("python3.*")))])


def sign() -> None:
    log("signing ad hoc")
    run(["codesign", "--force", "--deep", "--sign", "-", "--identifier", BUNDLE_ID, str(APP)])
    run(["codesign", "--verify", "--deep", "--strict", str(APP)])


def check(version: str, python: Path) -> None:
    """The runtime imports every core package from its own folders, Qt WebEngine renders, and the real launcher
    starts the app far enough to report its version."""
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
    web = dict(env, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
    log(run([str(python), "-I", "-B", "-c", WEB_PROBE], env=web, cwd=WORK, timeout=180).strip())
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
    log(f"copied {copy_app()} app files")
    compile_launcher(RESOURCES / "runtime")
    write_bundle_files(version)
    compile_bytecode(python)
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
