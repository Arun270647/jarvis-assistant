"""The repository's documentation stays true: docs/TOOLS.md matches the real tool registry (group, risk and
default permission of every tool), and every relative link in the Markdown files points to a file and heading
that exist."""
import json
import re
import unittest
from pathlib import Path

from src.core import config_store
from src.memory.store import MemoryStore
from src.router.action_router import ActionRouter
from src.tools import groups
from src.tools.policy import PolicyExecutor
from src.tools.registry import build_default_registry
from tests.fakes import temp_db

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + sorted(ROOT.glob("*.md"))


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


class ToolsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.agent.tasks import TaskManager
        cls.memory = MemoryStore(temp_db())
        cfg = config_store.load_config()
        cfg["tools"]["groups"] = groups.groups_setting("full")
        cls.reg = build_default_registry(ActionRouter(), cls.memory, cfg)
        TaskManager(cls.memory, PolicyExecutor(cls.reg, lambda: cfg), lambda: cfg, lambda ev: None).register_tools(cls.reg)

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()

    def rows(self):
        """{tool: (group title, risk, default)} from docs/TOOLS.md."""
        out, section = {}, ""
        for line in (ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
            m = re.match(r"\| `(\w+)` \|[^|]*\| (\w+) \| (\w+) \|", line)
            if m:
                out[m.group(1)] = (section, m.group(2), m.group(3))
        return out

    def test_every_tool_is_documented_with_its_group_risk_and_default(self):
        rows = self.rows()
        self.assertEqual(set(rows), set(self.reg.names()), "docs/TOOLS.md and the registry list different tools")
        for spec in self.reg.specs():
            group = groups.GROUPS[groups.group_of(spec.name)]["title"]
            self.assertEqual(rows[spec.name], (group, spec.risk, spec.permission), spec.name)


class LinkTests(unittest.TestCase):
    def test_relative_links_point_to_files_and_headings_that_exist(self):
        broken = []
        for doc in DOCS:
            text = doc.read_text(encoding="utf-8")
            text = re.sub(r"```.*?```", "", text, flags=re.S)          # code blocks aren't links
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if re.match(r"[a-z]+:", target) or target.startswith("#") and not target[1:]:
                    continue
                path, _, anchor = target.partition("#")
                dest = (doc.parent / path).resolve() if path else doc
                if not dest.exists():
                    broken.append(f"{doc.name}: {target} (no such file)")
                    continue
                if anchor and dest.suffix == ".md":
                    headings = [slug(h) for h in re.findall(r"^#+ (.+)$", dest.read_text(encoding="utf-8"), re.M)]
                    if anchor not in headings:
                        broken.append(f"{doc.name}: {target} (no such heading)")
        self.assertEqual(broken, [])

    def test_the_docs_mentioned_in_the_readme_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("INSTALL", "ARCHITECTURE", "TOOLS", "SECURITY_MODEL", "PHONE_APP", "EXTENSIONS",
                     "TROUBLESHOOTING", "PROVIDERS", "GETTING_STARTED"):
            self.assertIn(f"docs/{name}.md", readme)
        self.assertTrue(json.loads((ROOT / "mobile" / "package.json").read_text(encoding="utf-8"))["license"] == "Apache-2.0")


if __name__ == "__main__":
    unittest.main()
