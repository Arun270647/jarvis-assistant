"""Jarvis desktop app — main entry point.

    pythonw jarvis_gui.pyw              # no console window (double-click the file or the shortcut)
    pythonw jarvis_gui.pyw --minimized  # start as the floating orb (used by the Windows startup shortcut)

Settings, profile, database and logs live in the user's data folder (%APPDATA%\\Jarvis, or JARVIS_HOME);
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
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Jarvis could not start", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
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
