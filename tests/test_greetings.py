"""Window greetings (src/agent/greetings.py): "Jarvis is back." plus a line that is unique
forever, contains the user's first name (no name at all when the profile has none), is time-of-day aware and Claude-written with a local
fallback. The Claude CLI is replaced by a fake generator."""
import datetime
import json
import unittest

from src.agent.greetings import DEFAULT_PREFIX, GreetingPool, has_name, parse_batch, slot_for
from src.memory.store import MemoryStore
from tests.fakes import temp_db, wait_for

PROFILE = {"user": {"name": "Alex Morgan", "work": "Builds web shops"}}
PREFIX = DEFAULT_PREFIX.replace("{name}", "Jarvis") + " "     # the assistant's default name


def unique(spoken):
    """The unique line of a spoken greeting (what the greetings table stores)."""
    assert spoken.startswith(PREFIX), spoken
    return spoken[len(PREFIX):]


def at(hour):
    return lambda: datetime.datetime(2026, 10, 18, hour, 0)


class FakeTTS:
    def __init__(self):
        self.prepared = []

    def prepare(self, text):
        self.prepared.append(text)


class GreetingTests(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(temp_db())
        self.prompts = []

    def tearDown(self):
        self.memory.close()

    def pool(self, reply=None, hour=10, tts=None, profile=PROFILE, **cfg):
        def gen(prompt):
            self.prompts.append(prompt)
            if isinstance(reply, Exception):
                raise reply
            return reply or "[]"
        return GreetingPool({"greeting": dict({"min_pool": 3, "batch_size": 5}, **cfg)}, self.memory, tts,
                            profile=profile, generator=gen, clock=at(hour))

    def test_a_renamed_assistant_greets_with_its_own_name(self):
        pool = GreetingPool({"assistant": {"name": "Nova"}, "greeting": {"min_pool": 3, "batch_size": 5}}, self.memory,
                            profile=PROFILE, generator=lambda p: self.prompts.append(p) or "[]", clock=at(10))
        self.assertTrue(pool.take().startswith("Nova is back. "))
        pool._refill()
        self.assertIn('Nova always says "Nova is back."', self.prompts[-1])
        self.assertNotIn("Jarvis", self.prompts[-1])

    def test_slots(self):
        self.assertEqual([slot_for(h) for h in (6, 13, 18, 23, 2)], ["morning", "afternoon", "evening", "night", "night"])

    def test_parse_batch_tolerates_fences_and_drops_junk(self):
        text = "Sure!\n```json\n" + json.dumps([
            {"text": "  Morning,   Alex. Coffee first? ", "slot": "morning"},
            {"text": "Hi", "slot": "any"},                         # one word: too short
            {"text": "Systems green, boss.", "slot": "lunchtime"},  # unknown slot -> any
            "What are we building today?"]) + "\n```"
        self.assertEqual(parse_batch(text), [("Morning, Alex. Coffee first?", "morning"),
                                             ("Systems green, boss.", "any"),
                                             ("What are we building today?", "any")])
        self.assertEqual(parse_batch("no json here"), [])

    def test_parse_batch_with_a_name_drops_lines_without_it(self):
        text = json.dumps([{"text": "Morning, Alex. Coffee first?", "slot": "morning"},
                           {"text": "Systems green, boss.", "slot": "any"},
                           {"text": "Alexandria is far away today.", "slot": "any"},   # not the whole word
                           {"text": "what are we building, alex?", "slot": "any"}])
        self.assertEqual(parse_batch(text, name="Alex"), [("Morning, Alex. Coffee first?", "morning"),
                                                          ("what are we building, alex?", "any")])
        self.assertTrue(has_name("anything", ""))

    def test_greeting_starts_with_the_prefix_and_contains_the_name(self):
        self.memory.add_greetings([("Good to see you, Alex.", "any")])
        p = self.pool(reply=RuntimeError("offline"))
        self.assertEqual(p.take(), "Jarvis is back. Good to see you, Alex.")
        fallback = p.take()                                            # pool empty -> local fallback
        self.assertIn("Alex", unique(fallback))
        self.assertEqual(self.pool(prefix="Hello there.").take().split(" ")[:2], ["Hello", "there."])

    def test_stored_lines_without_the_name_are_skipped(self):
        self.memory.add_greetings([("Your move, boss.", "any"), ("Your move, Alex.", "any")])
        p = self.pool()
        self.assertEqual(p.take(), PREFIX + "Your move, Alex.")
        self.assertEqual(self.memory.count_unused_greetings(), 0)        # the name-less one is retired

    def test_greetings_are_never_repeated(self):
        self.memory.add_greetings([("Evening, Alex. Let's cook.", "any"), ("What are we shipping, Alex?", "any")])
        p = self.pool()
        said = [unique(p.take()) for _ in range(30)]
        self.assertEqual(len(said), len(set(s.lower() for s in said)), said)
        self.assertEqual(said[:2], ["Evening, Alex. Let's cook.", "What are we shipping, Alex?"])
        self.assertTrue(all("Alex" in s for s in said), said)
        # Claude repeating an old one adds nothing
        self.assertEqual(self.memory.add_greetings([("what are we shipping, alex?", "any")]), 0)

    def test_time_of_day_is_respected(self):
        self.memory.add_greetings([("Late one, Alex?", "night"), ("Morning, Alex.", "morning")])
        self.assertEqual(self.pool(hour=8).take(), PREFIX + "Morning, Alex.")
        self.assertEqual(self.pool(hour=23).take(), PREFIX + "Late one, Alex?")

    def test_refill_asks_claude_with_recent_greetings_and_name(self):
        self.memory.add_greetings([("Welcome back, Alex.", "any")])
        lines = [{"text": f"Fresh line number {i}, Alex.", "slot": "any"} for i in range(5)]
        lines.append({"text": "A line without the name.", "slot": "any"})   # dropped
        p = self.pool(reply=json.dumps(lines))
        p.take()
        self.assertTrue(wait_for(lambda: self.memory.count_unused_greetings() == 5))
        self.assertIn("Alex", self.prompts[0])
        self.assertIn("Welcome back, Alex.", self.prompts[0])          # don't repeat recent ones
        self.assertIn(PREFIX.strip(), self.prompts[0])                 # the model knows what comes first

    def test_claude_failure_falls_back_to_a_new_local_greeting(self):
        p = self.pool(reply=RuntimeError("not logged in"))
        first, second = p.take(), p.take()
        self.assertNotEqual(first, second)
        self.assertIn("Alex", unique(first))
        recent = self.memory.recent_greetings(10)
        self.assertIn(unique(first), recent)                           # stored, so never used again

    def test_without_a_name_nobody_is_addressed_by_name(self):
        p = self.pool(reply=RuntimeError("offline"), profile={})
        self.memory.add_greetings([("Good to see you. What's the plan?", "any")])
        self.assertEqual(p.take(), PREFIX + "Good to see you. What's the plan?")    # name rule off
        for _ in range(20):
            line = unique(p.take())                                    # local fallback lines
            self.assertNotIn("{name}", line)
            self.assertNotRegex(line, r"\s[,.?!]|,[.?!]|  ", line)      # no gap where the name was

    def test_without_a_name_claude_is_told_not_to_use_one(self):
        lines = [{"text": f"Fresh line number {i}.", "slot": "any"} for i in range(5)]
        p = self.pool(reply=json.dumps(lines), profile={"user": {}})
        p.take()
        self.assertTrue(wait_for(lambda: self.memory.count_unused_greetings() == 5))
        self.assertIn("Don't address the user by any name", self.prompts[0])
        self.assertNotIn("{", self.prompts[0].split("Reply with only")[0])

    def test_prepare_next_hands_the_next_greeting_to_tts_once(self):
        self.memory.add_greetings([("Hey Alex, ready when you are.", "any"), ("Your move, Alex.", "any")])
        tts = FakeTTS()
        p = self.pool(tts=tts)
        self.assertTrue(p.needs_prepare)
        self.assertTrue(p.prepare_next())
        self.assertFalse(p.prepare_next())                             # already prepared
        self.assertEqual(tts.prepared, ["Hey Alex, ready when you are."])
        self.assertEqual(p.take(), PREFIX + "Hey Alex, ready when you are.")    # the prepared one plays
        self.assertTrue(p.needs_prepare)


if __name__ == "__main__":
    unittest.main()
