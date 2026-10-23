"""Persistent agentic tasks: state, Claude's planning tools, independent verification.

A request becomes a tracked task when Claude calls create_plan. From then on:

  PLANNING -> RUNNING <-> WAITING_FOR_APPROVAL -> VERIFYING -> COMPLETED
                 |  \\-> BLOCKED (needs the user / limit reached / interrupted by restart)
                 |  \\-> PAUSED (user)            \\-> FAILED (verification kept failing)
                 \\-> CANCELLED (user)

Success is defined *before* execution: create_plan requires machine-checkable
success checks (command exit code / output, file exists / contains, process
running, HTTP status). finish_task does not take Claude's word for it: the
checks are run here, in Python, through the PolicyExecutor, and only a passing
run marks the task COMPLETED. A failing run goes back to Claude (bounded by
agent.task.max_verify_attempts) to diagnose and adapt.

Everything is stored in SQLite (tasks, task_events, tool_runs.task_id) so the
GUI can show it and tasks survive restarts. Interrupted tasks come back as
BLOCKED and are only resumed by the user.
"""
import threading
import time
from typing import Callable, Optional

from src.memory.store import TERMINAL_STATUSES
from src.tools.registry import CallBudget, ToolContext, ToolResult, ToolSpec

CHECK_TYPES = ("command", "file_exists", "file_contains", "process_running", "http_status")

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "minLength": 1, "maxLength": 300},
        "type": {"type": "string", "enum": list(CHECK_TYPES)},
        "command": {"type": "array", "items": {"type": "string"}, "description": "command: argv, run in cwd/project"},
        "cwd": {"type": "string"},
        "project": {"type": "string"},
        "expect_exit": {"type": "integer", "description": "command: expected exit code (default 0)"},
        "expect_text": {"type": "string", "description": "command/http_status: text that must appear in the output"},
        "path": {"type": "string", "description": "file_exists / file_contains"},
        "text": {"type": "string", "description": "file_contains: text that must be in the file"},
        "name": {"type": "string", "description": "process_running: executable name"},
        "url": {"type": "string", "description": "http_status"},
        "expect_status": {"type": "integer", "description": "http_status: expected code (default 200)"},
    },
    "required": ["description", "type"],
    "additionalProperties": False,
}

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 20},
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "depends_on": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "title"],
    "additionalProperties": False,
}


class TaskManager:
    def __init__(self, memory, executor, config_getter: Callable[[], dict], emit: Optional[Callable] = None):
        self.memory = memory
        self.executor = executor
        self._config = config_getter
        self._emit = emit or (lambda ev: None)
        self._lock = threading.RLock()
        self.request_ctx: Optional[ToolContext] = None    # the ctx of the request being processed
        self.request_text = ""
        self.speaker: Optional[Callable[[str], None]] = None
        self._last_spoken = 0.0
        self._pre_task = []      # inspection done in this request before the task existed

    # ------------------------------------------------------------ config / helpers

    def limits(self) -> dict:
        t = (self._config().get("agent", {}) or {}).get("task", {}) or {}
        return {"max_tool_calls": int(t.get("max_tool_calls", 80)), "max_seconds": float(t.get("max_seconds", 1800)),
                "max_verify_attempts": int(t.get("max_verify_attempts", 3)), "max_nudges": int(t.get("max_nudges", 3)),
                "speak_progress": bool(t.get("speak_progress", True))}

    def set_emitter(self, emit: Callable) -> None:
        self._emit = emit

    def snapshot(self, task_id: int, events: int = 0) -> Optional[dict]:
        t = self.memory.get_task(task_id)
        if t is None:
            return None
        if events:
            t["events"] = self.memory.task_events(task_id)[-events:]
        return t

    def _publish(self, task_id: int) -> None:
        snap = self.snapshot(task_id)
        if snap:
            self._emit({"type": "task_update", "task": snap})

    def event(self, task_id: int, kind: str, message: str, data: Optional[dict] = None) -> None:
        ev = self.memory.add_task_event(task_id, kind, message, data)
        print(f"[TASK #{task_id}] {kind}: {message}")
        self._emit({"type": "task_event", "event": ev})

    def set_status(self, task_id: int, status: str, **fields) -> dict:
        if status in TERMINAL_STATUSES:
            fields.setdefault("ended_at", time.time())
            fields.setdefault("control", None)
        task = self.memory.update_task(task_id, status=status, **fields)
        self._publish(task_id)
        return task

    def begin_request(self, ctx: ToolContext, text: str) -> None:
        with self._lock:
            self.request_ctx = ctx
            self.request_text = text
            self._pre_task = []

    def attach(self, ctx: ToolContext, task_id: int) -> None:
        ctx.task_id = task_id
        ctx.budget = CallBudget(self.limits()["max_tool_calls"])

    # ------------------------------------------------------------ context for Claude

    def context_lines(self) -> list:
        """Open tasks (so Claude can resume them) and recent outcomes (so it avoids failed approaches)."""
        lines = []
        open_tasks = self.memory.list_tasks(limit=5, statuses=("BLOCKED", "PAUSED", "WAITING_FOR_APPROVAL"))
        if open_tasks:
            lines.append("[context] Unfinished tasks (use resume_task if the user's message continues one):")
            for t in open_tasks:
                why = t.get("blocked_reason") or ""
                lines.append(f"  - #{t['id']} [{t['status']}] {t['objective']}" + (f" | {why[:200]}" if why else ""))
        recent = [t for t in self.memory.list_tasks(limit=6) if t["status"] in TERMINAL_STATUSES][:3]
        if recent:
            lines.append("[context] Recent task outcomes:")
            for t in recent:
                lines.append(f"  - #{t['id']} [{t['status']}] {t['objective']}: {(t.get('summary') or '')[:160]}")
        return lines

    def resume_prompt(self, task_id: int, reason: str, answer: str = "") -> str:
        t = self.memory.get_task(task_id)
        plan = t.get("plan") or []
        steps = "\n".join(f"  {s['id']}. [{s.get('status', 'pending')}] {s['title']}" + (f" — {s['note']}" if s.get("note") else "")
                          for s in plan) or "  (no plan yet: call create_plan first)"
        checks = "\n".join(f"  - {c['description']} ({c['type']})" for c in (t.get("checks") or [])) or "  (none)"
        events = "\n".join(f"  - {e['kind']}: {e['message'][:200]}" for e in self.memory.task_events(task_id)[-12:])
        interrupted = self.memory.interrupted_task_runs(task_id)
        lines = [f"[orchestrator] {reason}", f"Task #{task_id}: {t['objective']}",
                 f"Project: {t.get('project') or '(not set)'} | status before resume: {t['status']}",
                 "Plan:", steps, "Success checks:", checks, "Recent events:", events or "  (none)"]
        if t.get("blocked_reason"):
            lines.append(f"It was blocked because: {t['blocked_reason']}")
        if interrupted:
            lines.append("These actions were interrupted and their outcome is UNKNOWN. Inspect the actual state before "
                         "repeating any of them:")
            lines += [f"  - {r['tool']} {r['args']}" for r in interrupted]
        if answer:
            lines.append(f"The user's answer / instruction: {answer}")
        lines.append("Re-check the current state of anything that may have changed, then continue the remaining steps "
                     "and finish with finish_task (or block_task if you still cannot proceed).")
        return "\n".join(lines)

    # ------------------------------------------------------------ approval / tool observation

    def observe(self, ev: dict) -> None:
        """Pipeline events -> task timeline/status (tool calls, retries, approvals)."""
        t = ev.get("type")
        tid = ev.get("task_id") or (self.request_ctx.task_id if self.request_ctx else None)
        if not tid:
            # Inspection before create_plan: kept and attached to the task once it exists.
            if t in ("tool_call", "tool_result") and ev.get("category", "") != "task" and \
                    not (self.executor.registry.get(ev.get("tool") or "") and
                         self.executor.registry.get(ev["tool"]).category == "task"):
                with self._lock:
                    self._pre_task.append(dict(ev))
            return
        try:
            if t == "tool_call" and ev.get("category") != "task":
                self.event(tid, "tool", ev.get("purpose") or ev.get("tool"), {"tool": ev.get("tool"), "args": ev.get("args")})
            elif t == "tool_result" and self.executor.registry.get(ev.get("tool") or "") and \
                    self.executor.registry.get(ev["tool"]).category != "task":
                kind = "tool_ok" if ev.get("ok") else ("tool_blocked" if ev.get("status") in ("denied", "duplicate", "limit", "paused") else "tool_failed")
                first = (ev.get("content") or "").strip().splitlines()
                summary = f"{ev['tool']}: {ev['status']}" + (f" — {first[0][:180]}" if first else "")
                if ev.get("simulated"):
                    summary += " (simulated fault)"
                self.event(tid, kind, summary, {"tool": ev["tool"], "status": ev["status"], "ms": ev.get("ms")})
                task = self.memory.get_task(tid)
                if task:
                    self.memory.update_task(tid, tool_calls=(task.get("tool_calls") or 0) + 1)
            elif t == "tool_retry":
                self.event(tid, "retry", f"{ev['tool']} failed transiently; retry {ev['attempt']}", {"error": ev.get("error")})
            elif t == "approval_request":
                self.event(tid, "approval", f"Approval requested: {ev.get('title')}")
                self.set_status(tid, "WAITING_FOR_APPROVAL")
            elif t == "approval_resolved":
                self.event(tid, "approval", f"Approval {ev.get('decision')} for {ev.get('tool')} ({ev.get('via')})")
                task = self.memory.get_task(tid)
                if task and task["status"] == "WAITING_FOR_APPROVAL":
                    self.set_status(tid, "RUNNING")
        except Exception as e:
            print(f"[TASK] observe failed: {e}")

    # ------------------------------------------------------------ verification

    def run_checks(self, task_id: int) -> tuple:
        """Run every success check through the policy executor. Returns (all_passed, results)."""
        task = self.memory.get_task(task_id)
        checks = task.get("checks") or []
        ctx = ToolContext(session_id="verify", source="verify", task_id=task_id)
        results = []
        for i, c in enumerate(checks, 1):
            ok, evidence = self._run_check(c, ctx, task.get("project"))
            results.append({"n": i, "description": c["description"], "type": c["type"], "passed": ok,
                            "evidence": evidence[:600]})
            self.event(task_id, "check_pass" if ok else "check_fail",
                       f"{'PASS' if ok else 'FAIL'}: {c['description']}", {"evidence": evidence[:600]})
        return all(r["passed"] for r in results) and bool(results), results

    def _run_check(self, c: dict, ctx: ToolContext, project: Optional[str]) -> tuple:
        project = c.get("project") or project
        typ = c["type"]
        if typ == "command":
            if not c.get("command"):
                return False, "check has no command"
            args = {"command": c["command"]}
            if c.get("cwd"):
                args["cwd"] = c["cwd"]
            if project:
                args["project"] = project
            r = self.executor.execute("run_command", args, ctx)
            code = r.data.get("exit_code")
            if code is None:
                return False, r.content
            ok = code == int(c.get("expect_exit", 0)) and (not c.get("expect_text") or c["expect_text"] in r.content)
            return ok, r.content
        if typ in ("file_exists", "file_contains"):
            args = {"path": c.get("path", ""), "max_lines": 2000}
            if project:
                args["project"] = project
            r = self.executor.execute("read_file", args, ctx)
            if not r.ok:
                return False, r.content
            if typ == "file_exists":
                return True, r.content.splitlines()[0] if r.content else "exists"
            return (c.get("text", "") in r.content), f"looked for {c.get('text', '')!r} in {c.get('path')}"
        if typ == "process_running":
            r = self.executor.execute("check_process", {"name": c.get("name", "")}, ctx)
            return bool(r.ok and r.data.get("running")), r.content
        if typ == "http_status":
            r = self.executor.execute("http_check", {"url": c.get("url", "")}, ctx)
            status = r.data.get("status")
            ok = status == int(c.get("expect_status", 200)) and (not c.get("expect_text") or c["expect_text"] in r.content)
            return ok, r.content
        return False, f"unknown check type {typ}"

    # ------------------------------------------------------------ Claude's task tools

    def _current(self, ctx: ToolContext) -> Optional[int]:
        req = self.request_ctx
        return ctx.task_id or (req.task_id if req else None)

    def register_tools(self, reg) -> None:
        def create_plan(a, ctx):
            req = self.request_ctx
            tid = self._current(ctx)
            steps = [{"id": s["id"], "title": s["title"], "depends_on": s.get("depends_on", []), "status": "pending"}
                     for s in a["steps"]]
            ids = {s["id"] for s in steps}
            bad = [d for s in steps for d in s["depends_on"] if d not in ids]
            if bad:
                return ToolResult(ok=False, status="invalid", content=f"Unknown step ids in depends_on: {bad}")
            if tid is None:
                task = self.memory.create_task(a["objective"], project=a.get("project"),
                                               source=req.source if req else "text",
                                               session_id=req.session_id if req else "")
                tid = task["id"]
                if req is not None:
                    self.attach(req, tid)
                self.event(tid, "accepted", f"Objective accepted: {a['objective']}",
                           {"request": self.request_text[:500]})
                with self._lock:
                    pre, self._pre_task = self._pre_task, []
                for ev in pre:
                    ev["task_id"] = tid
                    self.observe(ev)
                if pre:
                    self.event(tid, "inspected", f"Inspection before planning: {sum(1 for e in pre if e['type'] == 'tool_result')} tool calls")
                verb = "created"
            else:
                task = self.memory.get_task(tid)
                old = task.get("plan") or []
                done = {s["id"]: s for s in old if s.get("status") in ("done", "skipped", "failed", "running")}
                for s in steps:
                    if s["id"] in done:
                        s["status"], s["note"] = done[s["id"]].get("status"), done[s["id"]].get("note")
                verb = "revised" if old else "created"
            fields = {"plan": steps, "objective": a["objective"]}
            if a.get("project"):
                fields["project"] = a["project"]
            if a.get("success_checks"):
                prev = (self.memory.get_task(tid) or {}).get("checks")
                fields["checks"] = a["success_checks"]
                if prev and prev != a["success_checks"]:
                    self.event(tid, "checks_changed", "Success criteria changed",
                               {"before": prev, "after": a["success_checks"]})
            elif not (self.memory.get_task(tid) or {}).get("checks"):
                return ToolResult(ok=False, status="invalid",
                                  content="success_checks are required: define how success will be verified before executing.")
            self.set_status(tid, "RUNNING", **fields)
            self.event(tid, "plan", f"Plan {verb}: " + "; ".join(f"{s['id']}. {s['title']}" for s in steps),
                       {"steps": steps, "checks": fields.get("checks")})
            return ToolResult(ok=True, content=f"Task #{tid} plan {verb} with {len(steps)} steps and "
                                               f"{len((self.memory.get_task(tid) or {}).get('checks') or [])} success checks. "
                                               "Execute it now; mark steps with update_step; call finish_task when done.",
                              data={"task_id": tid})

        def update_step(a, ctx):
            tid = self._current(ctx)
            if tid is None:
                return ToolResult(ok=False, status="invalid", content="No active task: call create_plan first")
            task = self.memory.get_task(tid)
            plan = task.get("plan") or []
            step = next((s for s in plan if s["id"] == a["step_id"]), None)
            if step is None:
                return ToolResult(ok=False, status="invalid", content=f"No step {a['step_id']}; steps: {[s['id'] for s in plan]}")
            pending_deps = [d for d in step.get("depends_on", [])
                            if next((s for s in plan if s["id"] == d), {}).get("status") not in ("done", "skipped")]
            if a["status"] in ("running", "done") and pending_deps:
                return ToolResult(ok=False, status="invalid",
                                  content=f"Step {step['id']} depends on unfinished steps {pending_deps}")
            step["status"] = a["status"]
            if a.get("note"):
                step["note"] = a["note"][:300]
            self.memory.update_task(tid, plan=plan)
            self.event(tid, "step", f"Step {step['id']} {a['status']}: {step['title']}" + (f" — {a['note']}" if a.get("note") else ""),
                       {"step": step})
            self._publish(tid)
            return ToolResult(ok=True, content=f"Step {step['id']} marked {a['status']}")

        def report_progress(a, ctx):
            tid = self._current(ctx)
            msg = a["message"].strip()
            if tid:
                self.event(tid, "progress", msg)
            self._emit({"type": "task_progress", "task_id": tid, "message": msg})
            lim = self.limits()
            req = self.request_ctx
            if (lim["speak_progress"] and self.speaker and req is not None and req.source == "voice"
                    and time.time() - self._last_spoken > 20):
                self._last_spoken = time.time()
                try:
                    self.speaker(msg)
                except Exception as e:
                    print(f"[TASK] progress speech failed: {e}")
            return ToolResult(ok=True, content="Progress shown to the user")

        def finish_task(a, ctx):
            tid = self._current(ctx)
            if tid is None:
                return ToolResult(ok=False, status="invalid", content="No active task to finish")
            task = self.memory.get_task(tid)
            if task["status"] in TERMINAL_STATUSES:
                return ToolResult(ok=False, status="invalid",
                                  content=f"Task #{tid} is already {task['status']}; nothing more to verify. Report the outcome.")
            open_steps = [st["id"] for st in task.get("plan") or [] if st.get("status") in (None, "pending", "running")]
            if open_steps:
                return ToolResult(ok=False, status="invalid", content=(
                    f"Steps {open_steps} are not marked yet. Call update_step for each (done, skipped or failed, with "
                    "a short note), then call finish_task again."))
            lim = self.limits()
            attempt = (task.get("verify_attempts") or 0) + 1
            self.set_status(tid, "VERIFYING", verify_attempts=attempt, summary=a["summary"][:2000])
            self.event(tid, "verifying", f"Verification attempt {attempt}: running {len(task.get('checks') or [])} checks")
            passed, results = self.run_checks(tid)
            report = "\n".join(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['description']}: "
                               f"{r['evidence'].strip().splitlines()[-1][:200] if r['evidence'].strip() else ''}"
                               for r in results)
            if passed:
                self.set_status(tid, "COMPLETED", verification=results)
                self.event(tid, "completed", "Verified and completed: " + a["summary"][:300])
                return ToolResult(ok=True, content="VERIFIED: every success check passed.\n" + report +
                                  "\nNow give the user a short spoken final report (what was done, what changed).")
            if attempt >= lim["max_verify_attempts"]:
                self.set_status(tid, "FAILED", verification=results,
                                summary=f"{a['summary'][:1500]} | Verification failed {attempt} times.")
                self.event(tid, "failed", f"Verification failed {attempt} times; giving up")
                return ToolResult(ok=False, status="failed", content="VERIFICATION FAILED (final):\n" + report +
                                  "\nThe task is marked FAILED. Stop and tell the user honestly what is still wrong.")
            self.set_status(tid, "RUNNING", verification=results)
            return ToolResult(ok=False, status="failed",
                              content=f"VERIFICATION FAILED (attempt {attempt} of {lim['max_verify_attempts']}):\n{report}\n"
                                      "Diagnose why, fix what you safely can, then call finish_task again. If it needs the "
                                      "user, call block_task.")

        def block_task(a, ctx):
            tid = self._current(ctx)
            if tid is None:
                return ToolResult(ok=False, status="invalid", content="No active task")
            reason = a["reason"] + (f" Question: {a['question']}" if a.get("question") else "")
            self.set_status(tid, "BLOCKED", blocked_reason=reason[:1000])
            self.event(tid, "blocked", reason[:500])
            return ToolResult(ok=True, content="Task marked BLOCKED. Tell the user briefly what you need.")

        def resume_task(a, ctx):
            req = self.request_ctx
            task = self.memory.get_task(a["task_id"])
            if task is None:
                return ToolResult(ok=False, status="invalid", content=f"No task #{a['task_id']}")
            if task["status"] in TERMINAL_STATUSES:
                return ToolResult(ok=False, status="invalid",
                                  content=f"Task #{task['id']} is {task['status']}; start a new task instead (create_plan).")
            if req is not None:
                self.attach(req, task["id"])
            self.memory.update_task(task["id"], control=None, blocked_reason=None, attempts=(task.get("attempts") or 0) + 1)
            self.set_status(task["id"], "RUNNING")
            self.event(task["id"], "resumed", "Resumed" + (f" with the user's answer: {a['user_answer']}" if a.get("user_answer") else ""))
            return ToolResult(ok=True, content=self.resume_prompt(task["id"], "Task resumed.", a.get("user_answer", "")))

        def note_capability_gap(a, ctx):
            tid = self._current(ctx)
            msg = f"Missing capability: {a['capability']} — {a['why']}"
            if tid:
                self.event(tid, "capability_gap", msg)
            self._emit({"type": "info", "detail": msg})
            return ToolResult(ok=True, content="Recorded. Tell the user this needs a new tool (a Jarvis extension they "
                                               "can add in tools_ext/); do not try to work around it.")

        reg.register(ToolSpec(
            name="create_plan",
            description="Turn the current request into a tracked multi-step task (or revise its plan). Use for any "
                        "objective that needs more than one or two actions. Inspect first, then give ordered steps "
                        "(with dependencies) and success_checks that prove the objective is met; the checks are run "
                        "independently when you call finish_task.",
            input_schema={"type": "object", "properties": {
                "objective": {"type": "string", "minLength": 3, "maxLength": 500},
                "project": {"type": "string"},
                "steps": {"type": "array", "items": STEP_SCHEMA, "minItems": 1, "maxItems": 30},
                "success_checks": {"type": "array", "items": CHECK_SCHEMA, "minItems": 1, "maxItems": 10}},
                "required": ["objective", "steps"]},
            handler=create_plan, category="task", purpose=lambda a: f"Plan: {a.get('objective')}"))
        reg.register(ToolSpec(
            name="update_step", description="Mark a plan step running, done, failed or skipped, with a short note.",
            input_schema={"type": "object", "properties": {
                "step_id": {"type": "string"}, "status": {"type": "string", "enum": ["running", "done", "failed", "skipped"]},
                "note": {"type": "string", "maxLength": 300}}, "required": ["step_id", "status"]},
            handler=update_step, category="task", purpose=lambda a: f"Step {a.get('step_id')} {a.get('status')}"))
        reg.register(ToolSpec(
            name="report_progress", description="Tell the user a one-sentence progress update during a long task.",
            input_schema={"type": "object", "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 200}},
                          "required": ["message"]},
            handler=report_progress, category="task", purpose=lambda a: a.get("message", "")))
        reg.register(ToolSpec(
            name="finish_task", description="Claim the task is done. Jarvis then runs the success checks itself; only "
                                            "passing checks complete the task. Read the result and adapt if they fail.",
            input_schema={"type": "object", "properties": {"summary": {"type": "string", "minLength": 1, "maxLength": 2000}},
                          "required": ["summary"]},
            handler=finish_task, category="task", timeout_s=1200, purpose=lambda a: "Verify and finish the task"))
        reg.register(ToolSpec(
            name="block_task", description="Stop the task because it cannot proceed safely or needs information or a "
                                           "decision from the user. Give the reason and the question.",
            input_schema={"type": "object", "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 600},
                                                           "question": {"type": "string", "maxLength": 400}},
                          "required": ["reason"]},
            handler=block_task, category="task", purpose=lambda a: f"Blocked: {a.get('reason')}"))
        reg.register(ToolSpec(
            name="resume_task", description="Continue an unfinished (BLOCKED or PAUSED) task, e.g. when the user answers "
                                            "its question or says to continue it. Returns the task's saved state.",
            input_schema={"type": "object", "properties": {"task_id": {"type": "integer", "minimum": 1},
                                                           "user_answer": {"type": "string", "maxLength": 1000}},
                          "required": ["task_id"]},
            handler=resume_task, category="task", purpose=lambda a: f"Resume task #{a.get('task_id')}"))
        reg.register(ToolSpec(
            name="note_capability_gap", description="Record that the objective needs a capability no tool provides.",
            input_schema={"type": "object", "properties": {"capability": {"type": "string", "maxLength": 200},
                                                           "why": {"type": "string", "maxLength": 400}},
                          "required": ["capability", "why"]},
            handler=note_capability_gap, category="task", purpose=lambda a: f"Gap: {a.get('capability')}"))
