"""Policy executor: the single gate between Claude's tool requests and the PC.

execute(name, args, ctx) does, in order:
  1. unknown tool        -> invalid
  2. JSON-schema check   -> invalid (Claude sees the validation error and can retry)
  3. cancel requested    -> cancelled; task paused by the user -> paused
  4. call budget         -> limit (max tool calls per request / task)
  5. idempotency         -> duplicate (identical non-idempotent call already done in this task,
                            or interrupted by a restart: outcome unknown, inspect first)
  6. permission          -> deny | ask (ApprovalBroker) | allow
  7. run handler in a worker thread with a timeout and the cancel event;
     transient failures are retried (spec.retries, exponential backoff)
  8. redact secrets, audit (tool_runs row linked to the task) + tool_call / tool_result events

Permission resolution: Settings override (tools.permissions[name]) or the
tool's default; "deny" always wins. Then, escalating allow -> ask:
  - spec.always_ask (delete a file, email outside the trusted list, shut down,
    type into a running program): asks even when Settings says allow, and a
    spoken yes is not enough unless voice_approval.high_risk is on;
  - a per-argument rule (e.g. profile "confirm": true on sleep);
  - the untrusted-content rule (tools.untrusted_asks, on by default): once a
    tool result in this request was untrusted (ToolResult.untrusted), tools
    that can change something (risk sensitive/destructive, not a read) ask, so
    instructions hidden in that content can't act on their own. After a web
    page, screen or other outside content, all of them; after the user's own
    project files or command output, all but the project tools (groups.py).

Fault injection (config agent.fault_injection, for tests and the recovery demo)
makes chosen calls fail with a clearly labelled SIMULATED error.
"""
import hashlib
import json
import threading
import time
from typing import Callable, Optional

import jsonschema

from src.core.redact import redact
from src.tools import groups
from src.tools.approvals import ApprovalBroker, ApprovalRequest
from src.tools.registry import ToolContext, ToolRegistry, ToolResult


class PolicyExecutor:
    def __init__(self, registry: ToolRegistry, config_getter: Callable[[], dict],
                 approvals: Optional[ApprovalBroker] = None, memory=None,
                 emit: Optional[Callable[[dict], None]] = None):
        self.registry = registry
        self._config = config_getter
        self.approvals = approvals
        self.memory = memory
        self._emit = emit or (lambda ev: None)
        self._call_ids = 0
        self._lock = threading.Lock()
        self._faults_used = {}

    def set_emitter(self, emit: Callable[[dict], None]) -> None:
        self._emit = emit

    # ------------------------------------------------------------- permission

    def permission_for(self, name: str, args: dict, tainted: str = "") -> str:
        spec = self.registry.get(name)
        return "deny" if spec is None else self._permission(spec, args, tainted)[0]

    def _permission(self, spec, args: dict, tainted: str = "") -> tuple:
        """(allow | ask | deny, why it asks: "" | "setting" | "rule" | "always" | "untrusted"). tainted is the
        request's taint level: "" | "project" | "outside"."""
        tools_cfg = self._config().get("tools", {}) or {}
        perm = (tools_cfg.get("permissions", {}) or {}).get(spec.name, spec.permission)
        if perm == "deny":
            return "deny", ""
        rule = False
        if spec.needs_confirmation is not None:
            try:
                rule = bool(spec.needs_confirmation(args))
            except Exception:
                rule = True
        if spec.always_asks(args):
            return "ask", "always"
        if rule:
            return "ask", "rule"
        if perm not in ("allow", "ask"):
            return "ask", "setting"
        if (perm == "allow" and tainted and tools_cfg.get("untrusted_asks", True)
                and spec.risk != "safe" and spec.category != "read"
                and (tainted == "outside" or not groups.project_tool(spec.name))):
            return "ask", "untrusted"
        return perm, "setting" if perm == "ask" else ""

    # ------------------------------------------------------------- execute

    @staticmethod
    def args_hash(name: str, args: dict) -> str:
        clean = {k: v for k, v in args.items() if k != "repeat"}
        return hashlib.sha1((name + json.dumps(clean, sort_keys=True, default=str)).encode()).hexdigest()[:16]

    def execute(self, name: str, args: Optional[dict], ctx: Optional[ToolContext] = None) -> ToolResult:
        ctx = ctx or ToolContext()
        args = dict(args or {})
        with self._lock:
            self._call_ids += 1
            call_id = self._call_ids
        spec = self.registry.get(name)
        purpose = spec.describe(args) if spec else name
        shown_args = json.loads(redact(json.dumps(args, default=str)))
        self._emit({"type": "tool_call", "call_id": call_id, "tool": name, "args": shown_args, "purpose": purpose,
                    "risk": spec.risk if spec else "unknown", "task_id": ctx.task_id,
                    "category": spec.category if spec else "action"})
        t0 = time.time()
        run_id = None
        h = self.args_hash(name, args)
        earlier = []
        if self.memory is not None:
            try:
                if ctx.task_id and spec is not None and not spec.idempotent:
                    earlier = self.memory.find_tool_runs(ctx.task_id, name, h)
                run_id = self.memory.start_tool_run(ctx.session_id, name, shown_args, task_id=ctx.task_id, args_hash=h)
            except Exception as e:
                print(f"[POLICY] Could not record tool run: {e}")

        decision = None
        result = self._execute(spec, name, args, ctx, earlier)
        if isinstance(result, tuple):
            result, decision = result
        result.content = redact(result.content)
        if result.untrusted:           # later risky calls in this request ask first
            level = groups.taint_level(name)
            if not ctx.tainted or (ctx.taint_level == "project" and level == "outside"):
                ctx.tainted, ctx.taint_level = name, level
        ms = int((time.time() - t0) * 1000)

        if self.memory is not None and run_id is not None:
            try:
                self.memory.finish_tool_run(run_id, result.status, result=result.content if result.ok else None,
                                            error=None if result.ok else result.content, decision=decision,
                                            duration_ms=ms)
            except Exception as e:
                print(f"[POLICY] Could not record tool result: {e}")
        print(f"[TOOL] {name}({shown_args}) -> {result.status}: {result.content[:200]} ({ms}ms)")
        self._emit({"type": "tool_result", "call_id": call_id, "tool": name, "ok": result.ok, "status": result.status,
                    "content": result.content, "dry_run": result.dry_run, "ms": ms, "decision": decision,
                    "task_id": ctx.task_id, "data": {k: v for k, v in result.data.items() if k != "images"}, "simulated": bool(result.data.get("simulated"))})
        return result

    def _execute(self, spec, name, args, ctx, earlier=()):
        if spec is None:
            return ToolResult(ok=False, status="invalid",
                              content=f"Unknown tool '{name}'. Available: {', '.join(self.registry.names())}")
        try:
            jsonschema.validate(args, spec.input_schema)
        except jsonschema.ValidationError as e:
            return ToolResult(ok=False, status="invalid", content=f"{name}: {e.message}")

        if ctx.cancel_event.is_set():
            return ToolResult(ok=False, status="cancelled", content="The user cancelled the request")
        control = self._task_control(ctx)
        if control == "pause":
            return ToolResult(ok=False, status="paused", content="The user paused this task. Stop now; do not call "
                                                                  "more tools. It will be resumed later.")
        if control == "cancel":
            return ToolResult(ok=False, status="cancelled", content="The user cancelled this task")
        needs_task = spec.requires_task(args) if callable(spec.requires_task) else bool(spec.requires_task)
        if needs_task and not ctx.task_id:
            return ToolResult(ok=False, status="invalid", content=(
                f"{name} changes the user's files or runs project commands, which is only allowed inside a tracked "
                "task. Finish inspecting, then call create_plan (steps + success_checks), and retry this call."))
        if ctx.budget is not None and not ctx.budget.take():
            return ToolResult(ok=False, status="limit",
                              content=f"Tool-call limit ({ctx.budget.max_calls}) reached for this request. Stop calling "
                                      "tools; finish (finish_task) or report what is left (block_task).")
        if earlier and not args.get("repeat"):
            done = [r for r in earlier if r["status"] in ("done", "dry_run")]
            unknown = [r for r in earlier if r["status"] in ("running", "interrupted")]
            if done:
                return ToolResult(ok=False, status="duplicate", content=(
                    f"An identical {name} call already succeeded earlier in this task "
                    f"(result: {(done[-1].get('result') or '')[:300]}). Not repeated. If it really must run again, "
                    "call it with repeat=true."))
            if unknown:
                return ToolResult(ok=False, status="duplicate", content=(
                    f"An identical {name} call was interrupted before Jarvis restarted, so its outcome is unknown. "
                    "Inspect the current state first (e.g. check_process, read_file, list_dir); only if it did not "
                    "take effect, call it again with repeat=true."))

        perm, why = self._permission(spec, args, ctx.taint_level if ctx.tainted else "")
        decision = "auto"            # allowed by policy without asking
        if perm == "deny":
            return ToolResult(ok=False, status="denied",
                              content=f"Tool '{name}' is blocked by the user's permission settings"), "deny"
        if perm == "ask":
            decision = self._ask(spec, args, ctx, why)
            if decision == "allow":
                decision = "approved"
            else:
                reason = {"deny": "The user denied this action", "timeout": "No answer before the approval timer ran out",
                          "cancelled": "The user cancelled the request"}.get(decision, "Not approved")
                status = "cancelled" if decision == "cancelled" else "denied"
                return ToolResult(ok=False, status=status, content=f"{reason}; nothing was executed"), decision

        return self._run_with_timeout(spec, args, ctx), decision

    def _ask(self, spec, args, ctx, why: str = "") -> str:
        if self.approvals is None:
            return "deny"
        cfg = self._config()
        voice_ok = why != "always" or bool((cfg.get("voice_approval", {}) or {}).get("high_risk", False))
        timeout_s = (cfg.get("tools", {}) or {}).get("approval_timeout_ms", 15000) / 1000.0
        if ctx.source == "phone":         # answered in the phone app: give the user time to pick it up
            timeout_s = max(timeout_s, float((cfg.get("phone", {}) or {}).get("approval_timeout_s", 180)))
        rows = []
        if spec.approval_rows:
            try:
                rows = spec.approval_rows(args)
            except Exception as e:
                rows = [{"k": "Tool", "v": spec.name, "sub": ""}, {"k": "Note", "v": f"details unavailable: {e}", "sub": ""}]
        else:
            rows = [{"k": "Tool", "v": spec.name, "sub": ""}] + [
                {"k": k, "v": str(v), "sub": ""} for k, v in args.items()]
        if why == "untrusted":
            where = ("your project folders" if ctx.taint_level == "project"
                     else "outside your project folders (a web page, a screen, a download)")
            rows.append({"k": "Why", "v": "Asked because this request read text it can't trust",
                         "sub": f"{ctx.tainted} returned text from {where}; instructions hidden in it must not act "
                                "on their own"})
        elif not voice_ok:
            rows.append({"k": "Why", "v": "This always needs your click or key",
                         "sub": "a spoken yes is not accepted for it"})
        purpose = spec.describe(args)
        kind = {"delegate_to_coding_agent": "Coding agent delegation", "system_command": "System command",
                "forget": "Memory deletion"}.get(spec.name, spec.risk.capitalize() + " action")
        from src.core.config_store import assistant_name
        name = assistant_name(cfg)
        if spec.name == "delegate_to_coding_agent":
            project = next((r["v"] for r in rows if r["k"] == "Project"), "the project")
            agent = next((r["v"] for r in rows if r["k"] == "Agent"), "the coding agent")
            title, spoken = f"Allow {agent} to work in “{project}”?", f"{name} wants to run {agent} in {project}."
        elif spec.name == "system_command":
            command = str(args.get("command", "")).replace("_", " ")
            title, spoken = f"Allow “{args.get('command')}” on this PC?", f"{name} wants to {command} the PC."
        else:
            title, spoken = f"Allow: {purpose}?", f"{name} wants to {purpose[:1].lower() + purpose[1:]}."
        spoken += " Allow?" if voice_ok else " To allow it, click Allow or press Y."
        req = ApprovalRequest(tool=spec.name, args=args, title=title, kind_label=kind, spoken=spoken,
                              rows=rows, timeout_s=timeout_s, source=ctx.source, voice_ok=voice_ok, reason=why)
        print(f"[APPROVAL] Asking: {title}")
        decision = self.approvals.request(req, cancel_event=ctx.cancel_event)
        print(f"[APPROVAL] {spec.name}: {decision} (via {req.via})")
        return decision

    def _task_control(self, ctx) -> Optional[str]:
        if not ctx.task_id or self.memory is None:
            return None
        try:
            task = self.memory.get_task(ctx.task_id)
            return task.get("control") if task else None
        except Exception:
            return None

    def _injected_fault(self, spec, args) -> Optional[ToolResult]:
        """Config agent.fault_injection: [{"tool", "times", "message", "transient", "match"}] (tests / demo)."""
        faults = ((self._config().get("agent", {}) or {}).get("fault_injection") or [])
        text = json.dumps(args, default=str)
        for i, f in enumerate(faults):
            if f.get("tool") != spec.name or (f.get("match") and f["match"] not in text):
                continue
            with self._lock:
                used = self._faults_used.get(i, 0)
                if used >= int(f.get("times", 1)):
                    continue
                self._faults_used[i] = used + 1
            msg = f.get("message", "Simulated failure")
            print(f"[POLICY] Fault injection: {spec.name} -> {msg}")
            return ToolResult(ok=False, status="failed", transient=bool(f.get("transient", False)),
                              content=f"[SIMULATED FAULT for recovery testing] {msg}", data={"simulated": True})
        return None

    def _run_with_timeout(self, spec, args, ctx) -> ToolResult:
        """Run with retries for transient failures (spec.retries, backoff 0.5s, 1s, 2s ...)."""
        attempts = 1 + max(0, int(spec.retries))
        result = None
        for attempt in range(attempts):
            result = self._injected_fault(spec, args) or self._run_once(spec, args, ctx)
            if result.ok or not result.transient or ctx.cancel_event.is_set() or attempt == attempts - 1:
                break
            delay = 0.5 * (2 ** attempt)
            print(f"[POLICY] {spec.name} transient failure, retry {attempt + 1}/{attempts - 1} in {delay:.1f}s")
            self._emit({"type": "tool_retry", "tool": spec.name, "attempt": attempt + 1, "error": result.content[:300],
                        "task_id": ctx.task_id})
            time.sleep(delay)
        if attempts > 1 and result is not None:
            result.data.setdefault("attempts", attempt + 1)
        return result

    def _run_once(self, spec, args, ctx) -> ToolResult:
        timeout = spec.timeout_s
        if timeout is None:
            timeout = float((self._config().get("tools", {}) or {}).get("default_timeout_s", 20))
        box = {}
        # The tool gets its own cancel event: set on user cancel or on timeout, so a
        # timed-out tool is stopped without cancelling the rest of the request.
        tool_ctx = ToolContext(session_id=ctx.session_id, source=ctx.source, cancel_event=threading.Event(),
                               task_id=ctx.task_id, tainted=ctx.tainted, taint_level=ctx.taint_level)

        def target():
            try:
                box["result"] = spec.handler(args, tool_ctx)
            except Exception as e:
                box["error"] = e

        th = threading.Thread(target=target, name=f"tool-{spec.name}", daemon=True)
        th.start()
        deadline = time.time() + timeout
        while th.is_alive() and time.time() < deadline:
            th.join(timeout=0.1)
            if ctx.cancel_event.is_set():
                tool_ctx.cancel_event.set()
                th.join(timeout=5 if spec.cancellable else 2)
                break
        if th.is_alive():
            tool_ctx.cancel_event.set()
            if ctx.cancel_event.is_set():
                return ToolResult(ok=False, status="cancelled",
                                  content=f"{spec.name} was asked to stop; it may still finish in the background")
            return ToolResult(ok=False, status="timeout", content=f"{spec.name} did not finish within {timeout:.0f}s")
        if ctx.cancel_event.is_set() and isinstance(box.get("result"), ToolResult) and box["result"].status == "failed":
            box["result"].status = "cancelled"
        if "error" in box:
            return ToolResult(ok=False, status="failed", content=f"{spec.name} failed: {box['error']}")
        result = box.get("result")
        if not isinstance(result, ToolResult):
            return ToolResult(ok=False, status="failed", content=f"{spec.name} returned no result")
        return result
