"""SQLite memory store."""
import threading
import unittest

from src.memory.store import MemoryStore
from tests.fakes import temp_db


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.m = MemoryStore(temp_db())
        self.addCleanup(self.m.close)

    def test_crud(self):
        a = self.m.add_memory("Favourite editor is VS Code", "preference")
        self.assertEqual(a["kind"], "preference")
        self.assertEqual(self.m.count_memories(), 1)
        b = self.m.update_memory(a["id"], content="Favourite editor is Neovim", pinned=True)
        self.assertEqual(b["content"], "Favourite editor is Neovim")
        self.assertEqual(b["pinned"], 1)
        self.assertTrue(self.m.delete_memory(a["id"]))
        self.assertFalse(self.m.delete_memory(a["id"]))
        self.assertIsNone(self.m.update_memory(999, content="x"))

    def test_empty_rejected_and_duplicates_merged(self):
        with self.assertRaises(ValueError):
            self.m.add_memory("   ")
        a = self.m.add_memory("Lives in Chennai")
        b = self.m.add_memory("lives in chennai")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(self.m.count_memories(), 1)

    def test_unknown_kind_becomes_note(self):
        self.assertEqual(self.m.add_memory("x y z", kind="weird")["kind"], "note")

    def test_search_ranks_by_overlap_and_context_includes_pinned(self):
        self.m.add_memory("The cat is called Miso", "fact")
        self.m.add_memory("Work laptop is a ThinkPad", "fact")
        pinned = self.m.add_memory("Call me Alex", "preference", pinned=True)
        hits = self.m.search_memories("what is my cat called")
        self.assertIn("Miso", hits[0]["content"])
        ctx = self.m.context_memories("what is my cat called", limit=5)
        ids = [r["id"] for r in ctx]
        self.assertEqual(ids[0], pinned["id"])
        self.assertTrue(any("Miso" in r["content"] for r in ctx))
        self.assertEqual(self.m.context_memories("anything", limit=0), [])

    def test_messages_and_tool_runs(self):
        self.m.add_message("s", "user", "hello", source="voice")
        self.m.add_message("s", "assistant", "hi there", meta={"tools": []})
        rows = self.m.recent_messages(10)
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
        self.assertEqual(rows[1]["meta"], {"tools": []})
        self.assertEqual(len(self.m.search_messages("there")), 1)
        rid = self.m.start_tool_run("s", "open_app", {"app_name": "notepad"})
        self.assertEqual(self.m.mark_interrupted_runs(), 1)
        self.m.finish_tool_run(rid, "done", result="ok", duration_ms=5)
        run = self.m.recent_tool_runs()[0]
        self.assertEqual((run["status"], run["args"]), ("done", {"app_name": "notepad"}))
        self.assertEqual(self.m.clear_messages(), 2)

    def test_concurrent_writers(self):
        def worker(n):
            for i in range(20):
                self.m.add_message("s", "user", f"t{n}-{i}")
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.m.recent_messages(500)), 80)


if __name__ == "__main__":
    unittest.main()
