"""Every test run gets its own throwaway JARVIS_HOME holding a fake user ("Alex", tests/fixtures), so the
tests never read or write the real user's settings, profile, database, logs, models or caches.

This runs when the `tests` package is imported, before any test module imports src.
"""
import atexit
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def remove_tree(path) -> None:
    """Delete a temp folder, including read-only files (git marks its object files read-only on Windows)."""
    def make_writable(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=make_writable)
        else:
            shutil.rmtree(path, onerror=make_writable)
    except OSError:
        pass                                                         # e.g. a file still open; it is only temp


HOME = Path(tempfile.mkdtemp(prefix="jarvis-tests-home-"))
PROJECTS = Path(tempfile.mkdtemp(prefix="jarvis-tests-projects-"))   # outside HOME: the data folders are protected
os.environ["JARVIS_HOME"] = str(HOME)
for _name in [n for n in os.environ if n.upper().startswith("JARVIS__")]:
    del os.environ[_name]                                            # a developer's overrides must not change results
atexit.register(remove_tree, HOME)
atexit.register(remove_tree, PROJECTS)

from tests.fakes import fixture_profile  # noqa: E402
import src.core.app_discovery as _app_discovery  # noqa: E402

_app_discovery.app_dirs = lambda: []                                # results must not depend on installed apps

(HOME / "user_profile.json").write_text(json.dumps(fixture_profile(PROJECTS), indent=2), encoding="utf-8")
