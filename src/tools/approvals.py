"""Approval broker: one pending permission request at a time, resolvable from
anywhere (GUI buttons, Y/N keys, typed yes/no, spoken yes/no) and expiring
to "deny" when the timer runs out.
"""
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

_ids = itertools.count(1)


@dataclass
class ApprovalRequest:
    tool: str
    args: dict
    title: str
    kind_label: str
    spoken: str
    rows: list
    timeout_s: float
    source: str = "text"
    id: int = field(default_factory=lambda: next(_ids))
    deadline: float = 0.0
    decision: Optional[str] = None      # allow | deny | timeout | cancelled
    via: str = ""
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_event(self) -> dict:
        return {"id": self.id, "tool": self.tool, "args": self.args, "title": self.title,
                "kind_label": self.kind_label, "spoken": self.spoken, "rows": self.rows,
                "timeout_s": self.timeout_s, "deadline": self.deadline, "source": self.source}

    @property
    def resolved(self) -> bool:
        return self._event.is_set()


class ApprovalBroker:
    def __init__(self, emit: Optional[Callable[[dict], None]] = None):
        self._emit = emit or (lambda ev: None)
        self._lock = threading.Lock()
        self._pending: Optional[ApprovalRequest] = None
        # Optional fn(request) run in the requesting thread before waiting; the
        # voice pipeline uses it to speak the question and listen for yes/no.
        self.prompter: Optional[Callable[[ApprovalRequest], None]] = None

    def set_emitter(self, emit: Callable[[dict], None]) -> None:
        self._emit = emit

    @property
    def pending(self) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._pending

    def request(self, req: ApprovalRequest, cancel_event: Optional[threading.Event] = None) -> str:
        """Block until the request is allowed, denied, cancelled or times out. Returns the decision."""
        req.deadline = time.time() + req.timeout_s
        with self._lock:
            if self._pending is not None:
                # Never stack prompts: a second concurrent request is denied outright.
                req.decision, req.via = "deny", "another approval is pending"
                return req.decision
            self._pending = req
        self._emit({"type": "approval_request", **req.to_event()})
        try:
            if self.prompter is not None:
                try:
                    self.prompter(req)
                except Exception as e:
                    print(f"[APPROVAL] Prompter failed: {e}")
            while not req._event.wait(timeout=0.1):
                if cancel_event is not None and cancel_event.is_set():
                    self._resolve(req, "cancelled", "cancel")
                elif time.time() >= req.deadline:
                    self._resolve(req, "timeout", "timer")
        finally:
            with self._lock:
                if self._pending is req:
                    self._pending = None
        self._emit({"type": "approval_resolved", "id": req.id, "tool": req.tool,
                    "decision": req.decision, "via": req.via})
        return req.decision

    def resolve(self, request_id: Optional[int], allowed: bool, via: str = "ui") -> bool:
        """Resolve the pending request (request_id None = whichever is pending)."""
        with self._lock:
            req = self._pending
        if req is None or (request_id is not None and req.id != request_id):
            return False
        return self._resolve(req, "allow" if allowed else "deny", via)

    def cancel_pending(self, via: str = "cancel") -> bool:
        with self._lock:
            req = self._pending
        return bool(req) and self._resolve(req, "cancelled", via)

    @staticmethod
    def _resolve(req: ApprovalRequest, decision: str, via: str) -> bool:
        if req._event.is_set():
            return False
        req.decision, req.via = decision, via
        req._event.set()
        return True


def classify_reply(text: str, affirmative: list, negative: list) -> str:
    """'yes' | 'no' | 'unknown' for a typed or spoken confirmation reply (whole words only)."""
    import re
    t = " " + re.sub(r"[^a-z' ]", " ", (text or "").lower()) + " "
    for w in negative:
        if f" {w.lower()} " in t:
            return "no"
    for w in affirmative:
        if f" {w.lower()} " in t:
            return "yes"
    return "unknown"
