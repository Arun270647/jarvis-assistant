"""requirements.lock agrees with requirements.txt. CI installs from the lock, so a requirement changed without
regenerating it (Dependabot's pull requests do exactly that) would be tested against the old versions."""
import re
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
REGENERATE = ("regenerate requirements.lock: uv pip compile requirements.txt --universal --python-version 3.11 "
              "--generate-hashes --no-header -o requirements.lock (keep its comment header)")


def locked_versions() -> dict:
    out = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line)
        if m:
            out[canonicalize_name(m.group(1))] = m.group(2)
    return out


class RequirementsLockTests(unittest.TestCase):
    def test_every_requirement_is_locked_at_a_version_it_allows(self):
        locked = locked_versions()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            req = Requirement(line)
            name = canonicalize_name(req.name)
            self.assertIn(name, locked, f"{req.name} is not in requirements.lock; {REGENERATE}")
            self.assertTrue(req.specifier.contains(locked[name], prereleases=True),
                            f"requirements.lock has {req.name}=={locked[name]}, outside '{req.specifier}'; {REGENERATE}")
