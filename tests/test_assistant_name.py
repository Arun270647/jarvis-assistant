"""The assistant's name (assistant.name): the product is Jarvis, but what the user sees and hears follows the
name they pick. A fixture with "Nova" runs the prompt, greeting, name-strip and UI string checks, and a static
check fails when a user-facing "Jarvis" literal appears in the web UI outside strings.js."""
import re
import unittest
from pathlib import Path

from src.agent.prompt import build_agent_system_prompt
from src.core import config_store
from src.pipeline import strip_address
from src.stt.transcriber import _build_initial_prompt

WEB = Path(__file__).resolve().parents[1] / "src" / "gui" / "web"
LITERAL = re.compile(r"\bJarvis\b|\bJARVIS\b")


def without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", text)


class NameRulesTests(unittest.TestCase):
    def test_names(self):
        for ok in ("Nova", "Jarvis", "Mary Jane", "D'Artagnan", "Jean-Luc", "Zoë", "Ава"):
            self.assertEqual(config_store.check_assistant_name(ok), (None, None), ok)
        for bad in ("", " ", "J", "R2D2", "x" * 21, "Nova!", "-Nova", "Nova--Bot"):
            self.assertIsNotNone(config_store.check_assistant_name(bad)[0], bad)
        error, warning = config_store.check_assistant_name("Computer")
        self.assertIsNone(error)
        self.assertIn("by accident", warning)
        self.assertEqual(config_store.check_assistant_name("  Mary   Jane "), (None, None), "spaces are tidied")

    def test_setting(self):
        self.assertEqual(config_store.assistant_name({}), "Jarvis")
        self.assertEqual(config_store.assistant_name({"assistant": {"name": "  Nova  "}}), "Nova")
        self.assertEqual(config_store.assistant_name({"assistant": {"name": "R2D2"}}), "Jarvis", "invalid: the default")
        self.assertEqual(config_store.defaults()["assistant"]["name"], "Jarvis")
        self.assertEqual(config_store.with_name(config_store.defaults()["greeting"]["prefix"], "Nova"), "Nova is back.")
        self.assertTrue(config_store.validate_update({"assistant.name": "x"}))
        self.assertEqual(config_store.validate_update({"assistant.name": "Nova"}), [])


class NameInUseTests(unittest.TestCase):
    def test_system_prompt(self):
        prompt = build_agent_system_prompt(["open_app", "create_plan"], {}, False, name="Nova")
        self.assertTrue(prompt.startswith("You are Nova, a voice assistant"))
        first_line, rest = prompt.split("\n", 1)
        self.assertIn("The app you run in is called Jarvis", first_line)
        self.assertNotIn("Jarvis", rest, "only the note about the app's name mentions Jarvis")
        self.assertNotIn("Jarvis", build_agent_system_prompt(["open_app"], {}, False, name="Jarvis").split("\n", 1)[1])

    def test_addressing_the_assistant_is_stripped(self):
        self.assertEqual(strip_address("Nova, open notepad", "Nova"), "open notepad")
        self.assertEqual(strip_address("hey nova open notepad", "Nova"), "open notepad")
        self.assertEqual(strip_address("Jarvis, what time is it", "Nova"), "what time is it")
        self.assertEqual(strip_address("Mary Jane, play music", "Mary Jane"), "play music")
        self.assertEqual(strip_address("novacaine prices", "Nova"), "novacaine prices", "whole words only")

    def test_speech_recognition_knows_the_name(self):
        prompt = _build_initial_prompt({}, "Nova")
        self.assertTrue(prompt.startswith("Nova voice assistant."))
        self.assertIn("Nova", prompt.split(".", 1)[1])


class WebStringsTests(unittest.TestCase):
    def test_no_name_literal_outside_strings_js(self):
        hits = []
        for path in sorted(WEB.glob("*.js")) + sorted(WEB.glob("*.html")):
            if path.name == "strings.js":
                continue
            for n, line in enumerate(without_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
                if LITERAL.search(line):
                    hits.append(f"{path.name}: {line.strip()[:120]}")
        self.assertEqual(hits, [], "user-facing text naming the assistant belongs in strings.js with {name}")

    def test_every_string_key_used_exists(self):
        source = (WEB / "strings.js").read_text(encoding="utf-8")
        keys = set(re.findall(r'^\s*"([\w.]+)":', source, flags=re.M))
        used = set()
        for path in list(WEB.glob("*.js")) + list(WEB.glob("*.html")):
            text = path.read_text(encoding="utf-8")
            used |= set(re.findall(r'\bt\("([\w.]+)"', text))
            used |= set(re.findall(r'data-s(?:-title|-aria|-title-key)?="([\w.]+)"', text))
        self.assertEqual(sorted(used - keys), [])
        self.assertIn("{name}", source)


if __name__ == "__main__":
    unittest.main()
