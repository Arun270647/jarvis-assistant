"""Greetings for when the user opens Jarvis's window: short, chill, and never the same twice.

- Every greeting is the fixed line `greeting.prefix` ("{name} is back." with the assistant's name, pre-rendered via
  tts.chatterbox.cache_phrases) followed by a unique line. When the profile has the user's name, that
  line always contains their first name; without a name it addresses nobody by name.
- The unique lines are written in batches by the utility model (llm.utility: by default the main
  provider's small, fast model, e.g. Haiku through `claude -p`), tagged with a time of day, and stored in
  SQLite (`greetings`; the unique line only, not the prefix). Each one is marked used when spoken and is
  never picked again; the table's UNIQUE text means a repeat is simply dropped, as is a line without the
  name.
- When fewer than `greeting.min_pool` are left, a background thread asks for more,
  passing the most recent ones so the style keeps moving.
- If the model is unreachable, a local mix-and-match fallback still produces a line that has never
  been used.
- prepare_next() hands the next unique line to the TTS engine (Chatterbox caches it), so it
  plays instantly when the window opens.
"""
import datetime
import json
import random
import re
import threading
from typing import Callable, Optional

SLOTS =("morning", "afternoon", "evening", "night")

DEFAULT_PREFIX = "{name} is back."

PROMPT = """You write the spoken greeting {assistant} (a calm, witty AI assistant) says when {who} opens \
its window on their PC. {assistant} always says "{prefix}" first; your line comes right after it, so it \
must follow that naturally (e.g. "{example}"). Write {count} lines.

Style: chill, cool, confident, warm, a little dry humour now and then. Spoken aloud, so natural \
speech: 4 to 14 words, no emojis, no hashtags, no quotes, no lists. {name_rule} Vary the shape: \
some questions, some statements, some playful. Don't start two with the same word. Don't repeat \
the opening line or say "I'm back". Nothing cheesy, no "How may I assist you".
About the user: {about}

Tag each with the time of day it fits: morning, afternoon, evening, night, or any.
Make about half of them "any".

Do not repeat or closely paraphrase any of these recent ones:
{recent}

Reply with only a JSON array: [{{"text": "...", "slot": "any"}}, ...]"""

# Fallback pieces (model unavailable). Every opener has the name; combinations are checked
# against the table, so a used one is never spoken again.
OPENERS = {
    "morning": ["Morning, {name}.", "Rise and grind, {name}.", "Good morning, {name}.", "Fresh day, {name}.",
                "Coffee's optional, {name}, I'm already up."],
    "afternoon": ["Afternoon, {name}.", "Good afternoon, {name}.", "Back at it, {name}.",
                  "Halfway through the day, {name}."],
    "evening": ["Evening, {name}.", "Good evening, {name}.", "Sun's down, systems up, {name}.",
                "Evening shift, {name}."],
    "night": ["Late one, {name}?", "Night owl mode, {name}.", "Burning the midnight oil, {name}?",
              "Still up, {name}?", "Quiet hours, {name}."],
    "any": ["Hey {name}.", "There you are, {name}.", "Good to see you, {name}.", "Systems green, {name}.",
            "Missed me, {name}?", "At your service, {name}.", "Right on time, {name}.", "Yo, {name}."],
}
CLOSERS = ["What are we building?", "What's the plan?", "Where do we start?", "I'm all ears.",
           "Ready when you are.", "Let's make something good.", "What's on the board?", "Say the word.",
           "Point me at a problem.", "What needs doing?", "Let's get it.", "Everything's warmed up.",
           "Your move.", "Let's keep it smooth.", "What are we shipping today?", "Let's cook."]


def slot_for(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def without_name(template: str) -> str:
    """A fallback piece with the {name} slot removed: "Missed me, {name}?" -> "Missed me?"."""
    return template.replace(", {name},", ",").replace(", {name}", "").replace(" {name}", "")


def has_name(text: str, name: str) -> bool:
    """True if text addresses the user by name (case-insensitive, whole word); always True with no name."""
    if not name:
        return True
    return re.search(rf"\b{re.escape(name)}\b", text or "", re.I) is not None


def parse_batch(text: str, name: str = "") -> list:
    """[(text, slot)] from the model's reply (tolerates code fences and stray prose around the JSON).
    With a name, lines that don't contain it are dropped."""
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for it in items:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        t = " ".join(str(it.get("text", "")).split()).strip().strip('"')
        slot = str(it.get("slot", "any")).lower()
        if 2 <= len(t.split()) <= 20 and len(t) <= 140 and has_name(t, name):
            out.append((t, slot if slot in SLOTS else "any"))
    return out


class GreetingPool:
    def __init__(self, config: dict, memory, tts=None, profile: Optional[dict] = None,
                 generator: Optional[Callable[[str], str]] = None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now):
        self.cfg = config.get("greeting", {}) or {}
        self.config = config
        self.memory = memory
        self.tts = tts
        self.clock = clock
        self.generator = generator or self._ask_utility
        user = (profile or {}).get("user") or {}
        self.name = (self.cfg.get("name") or user.get("name", "") or "").strip().split(" ")[0]   # "" = no name
        self.about = user.get("work", "")[:300]
        from src.core.config_store import assistant_name, with_name
        self.assistant = assistant_name(config)
        self.prefix = " ".join(with_name(self.cfg.get("prefix", DEFAULT_PREFIX), self.assistant).split())
        self._lock = threading.Lock()
        self._refilling = threading.Event()
        self._prepared = None              # greeting id whose audio was handed to the TTS
        self._used_local = set()           # fallback texts used when there is no memory store

    # ------------------------------------------------------------ choosing

    def _slots(self) -> tuple:
        return (slot_for(self.clock().hour), "any")

    def _candidate(self) -> Optional[dict]:
        if self.memory is None:
            return None
        rows = self.memory.unused_greetings(self._slots(), limit=20)
        if not rows:
            return None
        if self._prepared is not None:            # the one whose audio is ready goes first
            for r in rows:
                if r["id"] == self._prepared:
                    return r
        return rows[0]

    def take(self) -> str:
        """The greeting to speak now: "<prefix> <unique line>" (the line was never used before and
        contains the user's name). Also tops up the pool in the background."""
        with self._lock:
            row = self._candidate()
            while row is not None and not has_name(row["text"], self.name):
                self.memory.mark_greeting_used(row["id"])     # stored before names were required
                row = self._candidate()
            if row is not None:
                self.memory.mark_greeting_used(row["id"])
                text = row["text"]
            else:
                text = self._fallback()
            self._prepared = None
        self.refill_async()
        return f"{self.prefix} {text}" if self.prefix else text

    def _fallback(self) -> str:
        slot = slot_for(self.clock().hour)
        openers = OPENERS[slot] + OPENERS["any"]
        if not self.name:
            openers = [without_name(o) for o in openers]
        combos = [f"{o} {c}".format(name=self.name) for o in openers for c in CLOSERS]
        random.shuffle(combos)
        for text in combos:
            if self.memory is not None:
                if self.memory.add_greetings([(text, "any")]):
                    row = next((r for r in self.memory.unused_greetings(("any",), limit=500) if r["text"] == text), None)
                    if row:
                        self.memory.mark_greeting_used(row["id"])
                    return text
            elif text not in self._used_local:
                self._used_local.add(text)
                return text
        return f"Hey {self.name}, ready when you are." if self.name else "Hey, ready when you are."

    # ------------------------------------------------------------ refilling

    def refill_async(self) -> None:
        if self.memory is None or self._refilling.is_set():
            return
        if self.memory.count_unused_greetings() >= int(self.cfg.get("min_pool", 8)):
            return
        self._refilling.set()
        threading.Thread(target=self._refill, name="greeting-refill", daemon=True).start()

    def _refill(self) -> None:
        try:
            recent = self.memory.recent_greetings(40)
            if self.name:
                who, example = self.name, f"Good to see you, {self.name}. What are we building?"
                name_rule = f"EVERY line must contain the name {self.name}."
            else:
                who, example = "the user", "Good to see you. What are we building?"
                name_rule = "Don't address the user by any name."
            prompt = PROMPT.format(who=who, example=example, name_rule=name_rule, prefix=self.prefix,
                                   assistant=self.assistant,
                                   count=int(self.cfg.get("batch_size", 25)), about=self.about or "-",
                                   recent="\n".join(f"- {t}" for t in recent) or "- (none yet)")
            items = parse_batch(self.generator(prompt), name=self.name)
            added = self.memory.add_greetings(items)
            print(f"[GREETING] The utility model wrote {len(items)} greetings, {added} new")
        except Exception as e:
            print(f"[GREETING] Could not get new greetings: {e}")
        finally:
            self._refilling.clear()

    def _ask_utility(self, prompt: str) -> str:
        from src.agent.utility import utility_generator
        return utility_generator(self.config, timeout_s=float(self.cfg.get("timeout_s", 120)))(prompt)

    # ------------------------------------------------------------ audio

    def prepare_next(self) -> bool:
        """Let the TTS pre-render the next greeting (Chatterbox caches it). True if something was queued."""
        if self.tts is None or not hasattr(self.tts, "prepare"):
            return False
        with self._lock:
            row = self._candidate()
            if row is None or row["id"] == self._prepared:
                return False
            self._prepared = row["id"]
        self.tts.prepare(row["text"])
        return True

    @property
    def needs_prepare(self) -> bool:
        if self.tts is None or not hasattr(self.tts, "prepare"):
            return False
        row = self._candidate()
        return row is not None and row["id"] != self._prepared
