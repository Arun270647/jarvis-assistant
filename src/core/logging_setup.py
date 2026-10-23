"""Logging for windowed (pythonw) front-ends.

Under pythonw there is no console, so print() output from the pipeline would
be lost (sys.stdout is None). setup_logging() sends it to a rotating log file
and to any number of in-app listeners (the diagnostics panel).
"""
import io
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from src.core import paths

LOG_DIR = paths.log_dir()               # logs/ in the user's data folder
LOG_FILE = LOG_DIR / "jarvis.log"

logger = logging.getLogger("jarvis")

_listeners = []
_listeners_lock = threading.Lock()
_configured = False


class _RedactFilter(logging.Filter):
    """Masks keys, tokens and passwords (src/core/redact.py) in every line before it is written anywhere."""

    def filter(self, record):
        from src.core.redact import redact
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = redact(message)
        if clean != message or record.args:
            record.msg, record.args = clean, None
        return True


class _ListenerHandler(logging.Handler):
    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            return
        with _listeners_lock:
            listeners = list(_listeners)
        for fn in listeners:
            try:
                fn(record.levelname, line)
            except Exception:
                pass


class StreamToLogger:
    """File-like object that forwards writes to the jarvis logger, line by line."""

    def __init__(self, level: int):
        self._level = level
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, msg):
        if not msg:
            return 0
        with self._lock:
            self._buf += msg
            *lines, self._buf = self._buf.split("\n")
        for line in lines:
            if line.strip():
                logger.log(self._level, line.rstrip())
        return len(msg)

    def flush(self):
        with self._lock:
            rest, self._buf = self._buf, ""
        if rest.strip():
            logger.log(self._level, rest.rstrip())

    def isatty(self):
        return False

    def fileno(self):  # some libraries probe this; behave like a non-file stream
        raise io.UnsupportedOperation("StreamToLogger has no file descriptor")


def setup_logging(capture_std: bool = True, level: int = logging.DEBUG) -> Path:
    """Configure the rotating file log (idempotent). Returns the log file path."""
    global _configured
    if _configured:
        return LOG_FILE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(_RedactFilter())
    logger.addHandler(fh)

    lh = _ListenerHandler()
    lh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    lh.addFilter(_RedactFilter())
    logger.addHandler(lh)

    # Library warnings (warnings module, third-party loggers) go to the same file.
    logging.captureWarnings(True)
    pyw = logging.getLogger("py.warnings")
    pyw.addHandler(fh)
    pyw.propagate = False

    if capture_std:
        sys.stdout = StreamToLogger(logging.INFO)
        sys.stderr = StreamToLogger(logging.WARNING)

    def _excepthook(exc_type, exc, tb):
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        logger.critical(f"Unhandled exception in thread {args.thread.name if args.thread else '?'}",
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _configured = True
    return LOG_FILE


def add_listener(fn) -> None:
    """Register fn(levelname, line) to receive every log line (called from any thread)."""
    with _listeners_lock:
        _listeners.append(fn)


def remove_listener(fn) -> None:
    with _listeners_lock:
        if fn in _listeners:
            _listeners.remove(fn)


def tail(n: int = 300) -> list:
    """Last n lines of the current log file."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return []
