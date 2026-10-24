"""Persistent memory for Jarvis (SQLite, jarvis.db in the user's data folder).

Tables:
  memories     facts / preferences / project conventions (optionally scoped to a project)
  messages     the conversation (user transcripts, Jarvis replies, errors)
  tool_runs    every tool call the policy executor handled (audit, idempotency, Activity)
  tasks        agentic tasks: objective, status, plan, success checks, outcome
  task_events  the timeline of each task (accepted, plan, tool, approval, verification ...)
  llm_usage    tokens and estimated cost of every model request (Activity totals, the daily budget)

Schema changes are additive and applied on open (_migrate), so existing
databases keep their data.

One connection per thread (sqlite3 objects are thread-bound); WAL mode so the
GUI can read while the pipeline writes.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from src.core import paths

MEMORY_KINDS = ("fact", "preference", "note", "person", "task", "convention")

TASK_STATUSES = ("QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "BLOCKED", "PAUSED",
                 "VERIFYING", "COMPLETED", "FAILED", "CANCELLED")
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
ACTIVE_STATUSES = ("PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "VERIFYING")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'assistant',
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    source TEXT,
    text TEXT NOT NULL,
    meta TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    tool TEXT NOT NULL,
    args TEXT,
    status TEXT NOT NULL,
    decision TEXT,
    result TEXT,
    error TEXT,
    duration_ms INTEGER,
    started_at REAL NOT NULL,
    ended_at REAL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    objective TEXT NOT NULL,
    project TEXT,
    status TEXT NOT NULL,
    plan TEXT,
    checks TEXT,
    verification TEXT,
    summary TEXT,
    blocked_reason TEXT,
    control TEXT,
    source TEXT,
    session_id TEXT,
    parent_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    verify_attempts INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    ended_at REAL
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS greetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- every greeting is spoken at most once, ever
    slot TEXT NOT NULL DEFAULT 'any',           -- morning | afternoon | evening | night | any
    created_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    day TEXT NOT NULL,                          -- local date, YYYY-MM-DD
    role TEXT NOT NULL DEFAULT 'main',          -- main | utility
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,    -- uncached input
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL                               -- estimate; NULL when the model has no price
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_day ON llm_usage(day);
CREATE INDEX IF NOT EXISTS idx_tool_runs_started ON tool_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id);
"""

# Tables of removed features, dropped from existing databases (paired phones held token hashes).
DROPPED = "DROP TABLE IF EXISTS phone_devices;"

# Columns added after the first release of each table: (table, column, DDL type).
MIGRATIONS = [
    ("memories", "project", "TEXT"),
    ("memories", "approved", "INTEGER NOT NULL DEFAULT 1"),
    ("tool_runs", "task_id", "INTEGER"),
    ("tool_runs", "args_hash", "TEXT"),
]


class MemoryStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else paths.db_file()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._conns = []                 # every per-thread connection, so close() can release them all
        self._conns_lock = threading.Lock()
        with self._write_lock:
            conn = self._conn()
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.executescript(DROPPED)
            conn.executescript("CREATE INDEX IF NOT EXISTS idx_tool_runs_task ON tool_runs(task_id, args_hash);")
            conn.commit()

    # ------------------------------------------------------------ plumbing

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # Each thread uses only its own connection; check_same_thread=False only so
            # close() can release connections opened by threads that have exited.
            conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    @staticmethod
    def _migrate(conn) -> None:
        for table, column, ddl in MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _write(self, sql: str, params=()) -> int:
        with self._write_lock:
            conn = self._conn()
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def _read(self, sql: str, params=()) -> list:
        return [dict(r) for r in self._conn().execute(sql, params).fetchall()]

    def close(self):
        """Close every connection this store opened (call once, at shutdown)."""
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local = threading.local()

    # ------------------------------------------------------------ memories

    def add_memory(self, content: str, kind: str = "fact", source: str = "assistant", pinned: bool = False,
                   project: Optional[str] = None) -> dict:
        content = (content or "").strip()
        if not content:
            raise ValueError("Memory content is empty")
        if kind not in MEMORY_KINDS:
            kind = "note"
        # Avoid exact duplicates: touch the existing row instead.
        existing = self._read("SELECT * FROM memories WHERE lower(content) = lower(?)", (content,))
        now = time.time()
        if existing:
            self._write("UPDATE memories SET updated_at=? WHERE id=?", (now, existing[0]["id"]))
            return self.get_memory(existing[0]["id"])
        mid = self._write(
            "INSERT INTO memories(kind, content, source, pinned, project, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (kind, content, source, int(pinned), project or None, now, now),
        )
        return self.get_memory(mid)

    def get_memory(self, memory_id: int) -> Optional[dict]:
        rows = self._read("SELECT * FROM memories WHERE id=?", (int(memory_id),))
        return rows[0] if rows else None

    def update_memory(self, memory_id: int, content: Optional[str] = None, kind: Optional[str] = None,
                      pinned: Optional[bool] = None, project: Optional[str] = None) -> Optional[dict]:
        row = self.get_memory(memory_id)
        if row is None:
            return None
        content = row["content"] if content is None else content.strip()
        if not content:
            raise ValueError("Memory content is empty")
        kind = row["kind"] if kind is None else (kind if kind in MEMORY_KINDS else "note")
        pinned = row["pinned"] if pinned is None else int(bool(pinned))
        project = row.get("project") if project is None else (project.strip() or None)
        self._write("UPDATE memories SET content=?, kind=?, pinned=?, project=?, updated_at=? WHERE id=?",
                    (content, kind, pinned, project, time.time(), int(memory_id)))
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: int) -> bool:
        if self.get_memory(memory_id) is None:
            return False
        self._write("DELETE FROM memories WHERE id=?", (int(memory_id),))
        return True

    def list_memories(self, query: str = "", limit: int = 200) -> list:
        if query.strip():
            return self.search_memories(query, limit=limit)
        return self._read("SELECT * FROM memories ORDER BY pinned DESC, updated_at DESC LIMIT ?", (limit,))

    def search_memories(self, query: str, limit: int = 10) -> list:
        """Rank memories by how many query words they contain (simple, dependency-free)."""
        words = [w for w in _words(query) if len(w) > 2]
        if not words:
            return []
        clauses = " OR ".join(["lower(content) LIKE ?"] * len(words))
        rows = self._read(f"SELECT * FROM memories WHERE {clauses}", tuple(f"%{w}%" for w in words))
        for r in rows:
            text = r["content"].lower()
            r["score"] = sum(1 for w in words if w in text) + (0.5 if r["pinned"] else 0)
        rows.sort(key=lambda r: (r["score"], r["updated_at"]), reverse=True)
        return rows[:limit]

    def project_memories(self, project: str, limit: int = 20) -> list:
        return self._read("SELECT * FROM memories WHERE project=? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                          (project, limit))

    def context_memories(self, text: str, limit: int = 5) -> list:
        """Memories worth giving Claude for this turn: pinned ones plus the best matches."""
        if limit <= 0:
            return []
        pinned = self._read("SELECT * FROM memories WHERE pinned=1 ORDER BY updated_at DESC LIMIT ?", (limit,))
        seen = {r["id"] for r in pinned}
        out = list(pinned)
        for r in self.search_memories(text, limit=limit):
            if r["id"] not in seen and len(out) < limit:
                out.append(r)
                seen.add(r["id"])
        return out

    def count_memories(self) -> int:
        return self._read("SELECT COUNT(*) AS n FROM memories")[0]["n"]

    # ------------------------------------------------------------ conversation

    def add_message(self, session_id: str, role: str, text: str, source: Optional[str] = None,
                    meta: Optional[dict] = None) -> int:
        return self._write(
            "INSERT INTO messages(session_id, role, source, text, meta, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, role, source, text, json.dumps(meta) if meta else None, time.time()),
        )

    def recent_messages(self, limit: int = 60) -> list:
        rows = self._read("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        rows.reverse()
        for r in rows:
            r["meta"] = json.loads(r["meta"]) if r["meta"] else None
        return rows

    def search_messages(self, query: str, limit: int = 100) -> list:
        q = f"%{query.strip().lower()}%"
        rows = self._read("SELECT * FROM messages WHERE lower(text) LIKE ? ORDER BY id DESC LIMIT ?", (q, limit))
        for r in rows:
            r["meta"] = json.loads(r["meta"]) if r["meta"] else None
        return rows

    def clear_messages(self) -> int:
        n = self._read("SELECT COUNT(*) AS n FROM messages")[0]["n"]
        self._write("DELETE FROM messages")
        return n

    # ------------------------------------------------------------ tool runs

    def start_tool_run(self, session_id: str, tool: str, args: dict, task_id: Optional[int] = None,
                       args_hash: Optional[str] = None) -> int:
        return self._write(
            "INSERT INTO tool_runs(session_id, tool, args, status, started_at, task_id, args_hash) VALUES (?,?,?,?,?,?,?)",
            (session_id, tool, json.dumps(args, default=str), "running", time.time(), task_id, args_hash),
        )

    def find_tool_runs(self, task_id: int, tool: str, args_hash: str) -> list:
        """Earlier runs of exactly this call within a task (idempotency / restart reconciliation)."""
        return self._read("SELECT * FROM tool_runs WHERE task_id=? AND tool=? AND args_hash=? ORDER BY id",
                          (task_id, tool, args_hash))

    def task_tool_runs(self, task_id: int, limit: int = 200) -> list:
        rows = self._read("SELECT * FROM tool_runs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
        for r in rows:
            try:
                r["args"] = json.loads(r["args"]) if r["args"] else {}
            except json.JSONDecodeError:
                pass
        return rows

    def finish_tool_run(self, run_id: int, status: str, result: Optional[str] = None, error: Optional[str] = None,
                        decision: Optional[str] = None, duration_ms: Optional[int] = None) -> None:
        self._write(
            "UPDATE tool_runs SET status=?, result=?, error=?, decision=?, duration_ms=?, ended_at=? WHERE id=?",
            (status, result, error, decision, duration_ms, time.time(), run_id),
        )

    def recent_tool_runs(self, limit: int = 100) -> list:
        rows = self._read("SELECT * FROM tool_runs ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            try:
                r["args"] = json.loads(r["args"]) if r["args"] else {}
            except json.JSONDecodeError:
                pass
        return rows

    def mark_interrupted_runs(self) -> int:
        """Runs still 'running' from a previous process never finished; mark them."""
        n = self._read("SELECT COUNT(*) AS n FROM tool_runs WHERE status='running'")[0]["n"]
        if n:
            self._write("UPDATE tool_runs SET status='interrupted', ended_at=? WHERE status='running'", (time.time(),))
        return n


def _words(text: str) -> list:
    import re
    return re.findall(r"[a-z0-9']+", (text or "").lower())


# ---------------------------------------------------------------------- tasks
# (methods attached below to keep the class body readable)

_TASK_JSON = ("plan", "checks", "verification")


def _decode_task(row: dict) -> dict:
    for k in _TASK_JSON:
        if row.get(k):
            try:
                row[k] = json.loads(row[k])
            except json.JSONDecodeError:
                pass
    return row


def create_task(self, objective: str, project: Optional[str] = None, source: str = "text",
                session_id: str = "", parent_id: Optional[int] = None, status: str = "PLANNING") -> dict:
    now = time.time()
    tid = self._write(
        "INSERT INTO tasks(objective, project, status, source, session_id, parent_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)", (objective, project, status, source, session_id, parent_id, now, now))
    return self.get_task(tid)


def get_task(self, task_id: int) -> Optional[dict]:
    rows = self._read("SELECT * FROM tasks WHERE id=?", (int(task_id),))
    return _decode_task(rows[0]) if rows else None


def update_task(self, task_id: int, **fields) -> Optional[dict]:
    allowed = {"objective", "project", "status", "plan", "checks", "verification", "summary", "blocked_reason",
               "control", "attempts", "verify_attempts", "tool_calls", "ended_at", "session_id"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"Unknown task field: {k}")
        if k == "status" and v not in TASK_STATUSES:
            raise ValueError(f"Unknown task status: {v}")
        if k in _TASK_JSON and v is not None and not isinstance(v, str):
            v = json.dumps(v, default=str)
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return self.get_task(task_id)
    sets.append("updated_at=?")
    params += [time.time(), int(task_id)]
    self._write(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", tuple(params))
    return self.get_task(task_id)


def list_tasks(self, limit: int = 50, project: Optional[str] = None, statuses: Optional[tuple] = None) -> list:
    sql, params = "SELECT * FROM tasks", []
    where = []
    if project:
        where.append("project=?")
        params.append(project)
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        params += list(statuses)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_decode_task(r) for r in self._read(sql, tuple(params))]


def add_task_event(self, task_id: int, kind: str, message: str, data: Optional[dict] = None) -> dict:
    now = time.time()
    eid = self._write("INSERT INTO task_events(task_id, kind, message, data, created_at) VALUES (?,?,?,?,?)",
                      (int(task_id), kind, message, json.dumps(data, default=str) if data else None, now))
    return {"id": eid, "task_id": int(task_id), "kind": kind, "message": message, "data": data, "created_at": now}


def task_events(self, task_id: int, limit: int = 500) -> list:
    rows = self._read("SELECT * FROM task_events WHERE task_id=? ORDER BY id LIMIT ?", (int(task_id), limit))
    for r in rows:
        r["data"] = json.loads(r["data"]) if r["data"] else None
    return rows


def interrupted_task_runs(self, task_id: int) -> list:
    return [r for r in self.task_tool_runs(task_id) if r["status"] in ("running", "interrupted")]


def reconcile_after_restart(self) -> list:
    """Tasks that were active when the app stopped become BLOCKED (never auto-resumed)."""
    rows = self.list_tasks(limit=500, statuses=ACTIVE_STATUSES + ("QUEUED",))
    for t in rows:
        self.update_task(t["id"], status="BLOCKED", control=None,
                         blocked_reason="Interrupted when Jarvis stopped. Resume it to continue; "
                                        "unfinished actions are re-checked before anything is repeated.")
        self.add_task_event(t["id"], "interrupted", "Interrupted by a restart; waiting for you to resume.")
    return rows


# ------------------------------------------------------------ greetings

def add_greetings(self, items: list) -> int:
    """Store new greetings [(text, slot)]; texts seen before (used or not) are skipped. Returns how many were added."""
    now = time.time()
    added = 0
    with self._write_lock:
        conn = self._conn()
        for text, slot in items:
            cur = conn.execute("INSERT OR IGNORE INTO greetings(text, slot, created_at) VALUES (?,?,?)",
                               (text, slot or "any", now))
            added += cur.rowcount
        conn.commit()
    return added


def unused_greetings(self, slots: tuple = ("any",), limit: int = 50) -> list:
    marks = ",".join("?" * len(slots))
    return self._read(f"SELECT * FROM greetings WHERE used_at IS NULL AND slot IN ({marks}) ORDER BY id LIMIT ?",
                      (*slots, int(limit)))


def count_unused_greetings(self) -> int:
    return self._read("SELECT COUNT(*) AS n FROM greetings WHERE used_at IS NULL")[0]["n"]


def mark_greeting_used(self, greeting_id: int) -> None:
    self._write("UPDATE greetings SET used_at = ? WHERE id = ?", (time.time(), int(greeting_id)))


def recent_greetings(self, limit: int = 40) -> list:
    return [r["text"] for r in self._read(
        "SELECT text FROM greetings ORDER BY COALESCE(used_at, created_at) DESC LIMIT ?", (int(limit),))]


# ------------------------------------------------------------ model usage (src/agent/usage.py)

def add_llm_usage(self, provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0,
                  cache_read: int = 0, cache_write: int = 0, cost_usd: Optional[float] = None,
                  role: str = "main", day: Optional[str] = None) -> int:
    now = time.time()
    return self._write(
        "INSERT INTO llm_usage(created_at, day, role, provider, model, input_tokens, output_tokens, cache_read, "
        "cache_write, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (now, day or time.strftime("%Y-%m-%d", time.localtime(now)), role, provider, model, int(input_tokens),
         int(output_tokens), int(cache_read), int(cache_write), cost_usd))


def usage_by_day(self, days: int = 14) -> list:
    """Totals per local day, newest first: requests, tokens, estimated cost (unpriced = requests without one)."""
    return self._read(
        "SELECT day, COUNT(*) AS requests, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
        "SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write, COALESCE(SUM(cost_usd), 0) AS cost_usd, "
        "SUM(cost_usd IS NULL) AS unpriced FROM llm_usage GROUP BY day ORDER BY day DESC LIMIT ?", (int(days),))


def usage_cost_on(self, day: str) -> float:
    rows = self._read("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_usage WHERE day = ?", (day,))
    return float(rows[0]["c"]) if rows else 0.0


for _fn in (create_task, get_task, update_task, list_tasks, add_task_event, task_events,
            interrupted_task_runs, reconcile_after_restart, add_greetings, unused_greetings,
            count_unused_greetings, mark_greeting_used, recent_greetings, add_llm_usage, usage_by_day, usage_cost_on):
    setattr(MemoryStore, _fn.__name__, _fn)
