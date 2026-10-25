"""Jarvis desktop app — main entry point.

    pythonw jarvis_gui.pyw              # no console window (double-click the file or the shortcut)
    pythonw jarvis_gui.pyw --minimized  # start as the floating orb (used by the start-at-sign-in shortcut)
    python jarvis_gui.pyw --diagnose    # save a redacted diagnostic report for a bug report, and show it
    python jarvis_gui.pyw --version     # print the version (the Windows build checks it)

Settings, profile, database and logs live in the user's data folder (%APPDATA%\\Jarvis, ~/Library/Application
Support/Jarvis on macOS, or JARVIS_HOME);
logs: logs/jarvis.log there (also visible in Settings > Diagnostics).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _fatal(message: str) -> None:
    """Startup failed before the UI existed: log it and show a native message box."""
    try:
        from src.core.logging_setup import setup_logging, logger
        setup_logging(capture_std=False)
        logger.critical(message)
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            from src.core import winproc                 # standard library only, so it loads when packages are missing
            winproc.run_hidden(["/usr/bin/osascript", "-e", "on run argv", "-e",
                                'display alert "Jarvis could not start" message (item 1 of argv) as critical',
                                "-e", "end run", message], timeout=600)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Jarvis could not start", 0x10)
    except Exception:
        pass


def _diagnose() -> int:
    from src.core.diagnose import save_report
    path = save_report()
    print(f"Diagnostic report saved: {path}")
    try:
        from src.platform import current
        current().open_path(str(path))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    if "--version" in sys.argv:
        from src.version import __version__
        print(__version__)
        sys.exit(0)
    if "--diagnose" in sys.argv:
        sys.exit(_diagnose())
    try:
        from src.gui.qt_app import run
    except ImportError as e:
        _fatal(f"A required package is missing: {e}.\n\nInstall the dependencies with:\n"
               f"    python -m pip install -r requirements.txt")
        sys.exit(1)
    try:
        sys.exit(run())
    except Exception as e:
        import traceback
        _fatal(f"Unexpected startup error: {e}\n\n{traceback.format_exc()[-1500:]}")
        sys.exit(1)
