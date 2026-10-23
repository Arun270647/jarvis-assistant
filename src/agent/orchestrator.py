"""Controlled agent loop around the AI (any backend).

Each user request goes to the agent (with memory + open-task context). Simple
requests finish in one run. When the model turns the request into a task
(create_plan), the orchestrator keeps the task moving until it reaches a real
end state:

    run the agent -> look at the task in SQLite ->
        COMPLETED / FAILED / BLOCKED      -> report
        paused / cancelled by the user    -> PAUSED / CANCELLED
        model or provider error           -> BLOCKED (with the error)
        time or tool-call budget used up  -> BLOCKED (resume to continue)
        still RUNNING (the model stopped) -> continue prompt (at most agent.task.max_nudges)

Inside each run the model itself loops over tools (Understand / Inspect / Plan /
Execute / Observe / Adapt); the orchestrator enforces limits, persistence and
the verification gate (finish_task in tasks.py).
"""
import time
from typing import Callable

from src.agent import Agent
from src.agent.base import AgentResult
from src.agent.tasks import TaskManager
from src.memory.store import TERMINAL_STATUSES
from src.tools.registry import CallBudget, ToolContext


class Orchestrator:
    def __init__(self, agent: Agent, tasks: TaskManager, config_getter: Callable[[], dict]):
        self.agent = agent
        self.tasks = tasks
        self.memory = tasks.memory
        self._config = config_getter
        self.agent.context_providers.append(tasks.context_lines)

    @property
    def name(self):
        return self.agent.name

    @property
    def model(self):
        return self.agent.model

    def load(self):
        self.agent.load()

    def unload(self):
        self.agent.unload()

    def cancel(self):
        self.agent.cancel()

    def _request_budget(self) -> CallBudget:
        return CallBudget(int(self._config().get("agent", {}).get("max_tool_calls_per_request", 25)))

    # ------------------------------------------------------------ entry points

    def handle(self, text: str, source: str, ctx: ToolContext, emit: Callable) -> AgentResult:
        """A user request (voice or typed)."""
        ctx.budget = ctx.budget or self._request_budget()
        self.tasks.begin_request(ctx, text)
        started = time.time()
        res = self.agent.handle(text, source, ctx, emit)
        return self._drive(res, ctx, emit, started)

    def resume(self, task_id: int, ctx: ToolContext, emit: Callable, answer: str = "") -> AgentResult:
        """GUI Resume (or voice/typed via resume_task): continue a BLOCKED / PAUSED task."""
        task = self.memory.get_task(task_id)
        if task is None:
            return AgentResult(reply=f"There is no task {task_id}.", ok=False, error="unknown task")
        if task["status"] in TERMINAL_STATUSES:
            return AgentResult(reply=f"Task {task_id} is already {task['status'].lower()}. Use retry instead.")
        self.tasks.begin_request(ctx, f"[resume task #{task_id}]")
        self.tasks.attach(ctx, task_id)
        prompt = self.tasks.resume_prompt(task_id, "The user resumed this task.", answer)   # before the status changes
        self.memory.update_task(task_id, control=None, blocked_reason=None, attempts=(task.get("attempts") or 0) + 1)
        self.tasks.set_status(task_id, "RUNNING")
        self.tasks.event(task_id, "resumed", "Resumed by the user" + (f": {answer}" if answer else ""))
        started = time.time()
        res = self.agent.handle(prompt, ctx.source, ctx, emit)
        return self._drive(res, ctx, emit, started)

    def retry(self, task_id: int, ctx: ToolContext, emit: Callable) -> AgentResult:
        """GUI Retry: a new task for the same objective, informed by the failed attempt."""
        old = self.memory.get_task(task_id)
        if old is None:
            return AgentResult(reply=f"There is no task {task_id}.", ok=False, error="unknown task")
        new = self.memory.create_task(old["objective"], project=old.get("project"), source=ctx.source,
                                      session_id=ctx.session_id, parent_id=task_id)
        self.tasks.begin_request(ctx, f"[retry of task #{task_id}]")
        self.tasks.attach(ctx, new["id"])
        if old.get("checks"):
            self.memory.update_task(new["id"], checks=old["checks"])
        self.tasks.event(new["id"], "accepted", f"Retry of task #{task_id}: {old['objective']}")
        self.tasks.set_status(new["id"], "PLANNING")
        last = self.memory.task_events(task_id)[-10:]
        prompt = "\n".join([
            f"[orchestrator] The user asked to retry task #{task_id} as new task #{new['id']}.",
            f"Objective: {old['objective']}",
            f"The previous attempt ended {old['status']}: {old.get('summary') or old.get('blocked_reason') or ''}",
            "Its last events:", *[f"  - {e['kind']}: {e['message'][:200]}" for e in last],
            "Re-inspect the current state, avoid the approach that failed, call create_plan for this task, execute, "
            "and finish with finish_task."])
        started = time.time()
        res = self.agent.handle(prompt, ctx.source, ctx, emit)
        return self._drive(res, ctx, emit, started)

    # ------------------------------------------------------------ the loop

    def _drive(self, res: AgentResult, ctx: ToolContext, emit: Callable, started: float) -> AgentResult:
        lim = self.tasks.limits()
        nudges = 0
        while True:
            tid = ctx.task_id
            if not tid:
                return res                        # a simple request, no task
            task = self.memory.get_task(tid)
            status, control = task["status"], task.get("control")
            res.task_id = tid

            if control == "pause":
                self.tasks.set_status(tid, "PAUSED", control=None, blocked_reason="Paused by the user")
                self.tasks.event(tid, "paused", "Paused by the user")
                res.cancelled, res.ok = False, True
                return self._final(res, tid, "I've paused the task. Resume it when you're ready.")
            if control == "cancel" or res.cancelled or ctx.cancel_event.is_set():
                if status not in TERMINAL_STATUSES:
                    self.tasks.set_status(tid, "CANCELLED", summary=(task.get("summary") or "") + " Cancelled by the user.")
                    self.tasks.event(tid, "cancelled", "Cancelled by the user")
                res.cancelled = True
                return res
            if status in ("COMPLETED", "FAILED", "BLOCKED"):
                fallback = {"COMPLETED": "The task is done and verified.",
                            "FAILED": "I couldn't complete the task; the details are in the agent workspace.",
                            "BLOCKED": f"I'm blocked: {task.get('blocked_reason') or 'I need your input.'}"}[status]
                return self._final(res, tid, res.reply or fallback)
            if not res.ok:
                reason = f"The AI stopped with an error: {res.error}"
                self.tasks.set_status(tid, "BLOCKED", blocked_reason=reason[:1000])
                self.tasks.event(tid, "blocked", reason[:500])
                res.ok = True                     # reported through the task, spoken below
                return self._final(res, tid, res.spoken or "I hit an error talking to the AI, so I paused the task. "
                                                           "You can resume it.")
            elapsed = time.time() - started
            if elapsed > lim["max_seconds"] or (ctx.budget and ctx.budget.used >= ctx.budget.max_calls):
                what = "time limit" if elapsed > lim["max_seconds"] else "tool-call limit"
                self.tasks.set_status(tid, "BLOCKED", blocked_reason=f"Reached the {what}; resume to continue.")
                self.tasks.event(tid, "limit", f"Stopped at the {what}")
                return self._final(res, tid, f"I stopped at the {what} before finishing. You can resume the task.")
            if nudges >= lim["max_nudges"]:
                self.tasks.set_status(tid, "BLOCKED", blocked_reason="The AI kept stopping without finishing or verifying.")
                self.tasks.event(tid, "blocked", "Stopped without finishing after repeated continue prompts")
                return self._final(res, tid, res.reply or "I stopped before finishing; the task is waiting for you.")
            nudges += 1
            self.tasks.event(tid, "continue", f"Not finished yet ({status}); asking the AI to continue ({nudges}/{lim['max_nudges']})")
            prompt = ("[orchestrator] Task #%d is still %s and has not been verified. Continue with the remaining "
                      "steps now. When everything is done call finish_task; if you cannot proceed safely or need the "
                      "user, call block_task. Last thing you said: %r" % (tid, status, (res.reply or "")[:300]))
            res = self.agent.backend.run(prompt, ctx, emit)

    def _final(self, res: AgentResult, tid: int, reply: str) -> AgentResult:
        res.reply = reply
        res.task_id = tid
        task = self.memory.get_task(tid)
        res.task_status = task["status"] if task else None
        return res
