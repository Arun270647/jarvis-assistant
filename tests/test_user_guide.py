"""The user guide (docs/GETTING_STARTED.md) and the in-app ? buttons that open it: every anchor a button uses is a
real heading, and every screenshot the guide shows exists."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "GETTING_STARTED.md"


def github_anchor(heading: str) -> str:
    """How GitHub turns a heading into a link anchor."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


class UserGuideTests(unittest.TestCase):
    def setUp(self):
        self.text = GUIDE.read_text(encoding="utf-8")
        self.anchors = {github_anchor(m) for m in re.findall(r"^#{1,6} (.+)$", self.text, flags=re.M)}

    def test_help_buttons_point_at_real_sections(self):
        web = ROOT / "src" / "gui" / "web"
        used = set(re.findall(r'"([0-9a-z][0-9a-z-]+)"', re.search(r"const HELP = \{(.*?)\};", (web / "app.js").read_text(encoding="utf-8"), re.S).group(1)))
        used = {u for u in used if "-" in u and not u.startswith(("tools-and", "general"))} | \
            set(re.findall(r'openHelp", "([^"]+)"', (web / "setup.js").read_text(encoding="utf-8")))
        self.assertTrue(used)
        self.assertEqual(sorted(used - self.anchors), [])

    def test_screenshots_exist(self):
        images = re.findall(r"\]\((images/[^)]+)\)", self.text)
        self.assertGreaterEqual(len(images), 5)
        for rel in images:
            self.assertTrue((GUIDE.parent / rel).exists(), rel)

    def test_internal_links_resolve(self):
        for target in re.findall(r"\]\((?!https?://|images/)([^)#]+)(?:#[^)]*)?\)", self.text):
            self.assertTrue((GUIDE.parent / target).exists(), target)


if __name__ == "__main__":
    unittest.main()
