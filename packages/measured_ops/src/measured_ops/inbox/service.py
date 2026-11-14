"""List runs paused at the supervisor's gate, and decide them (C4, ADR-0017). No web code.

- A run is "waiting" when its latest checkpoint is paused at `gate` with an interrupt.
  Threads are found from the checkpointer itself (`alist(None)`): no index of our own.
  That scans every checkpoint, which is fine at v0 volume (revisit in C10).
- An action the policy has tightened to "never" since the pause stays listed, marked
  `blocked_by_policy`: hiding it would leave the run paused forever. Only rejecting
  ends it; the gate refuses an approval (`check_decision`).
- `decide` resumes with `Command(resume={interrupt_id: decision})`, never
  `ainvoke(None)`: after a refused answer the refused value is still the thread's
  pending resume, and a bare re-run would replay it (ADR-0016).
- Double taps: the interrupt id is the real guard (an answer for an interrupt that is
  no longer pending is `AlreadyDecided`). The per-thread lock only serialises two taps
  arriving at once, and it is in-process, so the inbox runs as one process (ADR-0017).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command
from pydantic import ValidationError

from measured_ops.contracts import ApprovalDecision, PendingAction, Task
from measured_ops.policy import ActionBlockedError, ApprovalPolicy

log = logging.getLogger("measured_ops.inbox")

RECURSION_LIMIT = 40


class AlreadyDecided(LookupError):
    """The run isn't waiting on this interrupt any more (decided, or never paused)."""


class Refused(ValueError):
    """The gate (or the decision model) refused this answer. The run is still paused."""


@dataclass(frozen=True)
class PendingItem:
    """One action waiting at the gate, as the inbox shows it."""

    thread_id: str
    interrupt_id: str
    paused_at: str  # ISO timestamp of the paused checkpoint
    task_id: str
    agent: str
    goal: str
    action_id: str
    type: str
    target: str
    preview: str
    risk_level: str
    response_schema: dict[str, Any]
    blocked_by_policy: bool


@dataclass(frozen=True)
class Outcome:
    thread_id: str
    approved: bool
    still_paused: bool  # the run paused again, on its next action


def _cfg(thread_id: str) -> Any:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def _reason(exc: ValidationError) -> str:
    msgs = (str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
    return "; ".join(msgs) or str(exc)


class Inbox:
    """Wraps a compiled supervisor graph that has a checkpointer.

    `graph` is typed `Any` so tests can pass a thin wrapper; it must offer
    `checkpointer`, `aget_state` and `ainvoke` like a `CompiledStateGraph`.
    """

    def __init__(self, graph: Any, policy: ApprovalPolicy) -> None:
        if getattr(graph, "checkpointer", None) is None:
            raise ValueError("the inbox needs a supervisor graph compiled with a checkpointer")
        self.graph = graph
        self.policy = policy
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}  # so a lock is dropped once nobody holds it

    async def _thread_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        async for checkpoint in self.graph.checkpointer.alist(None):
            seen.setdefault(checkpoint.config["configurable"]["thread_id"], None)
        return list(seen)

    async def get(self, thread_id: str) -> PendingItem | None:
        """The action `thread_id` is waiting on, or None if it isn't waiting."""
        snap = await self.graph.aget_state(_cfg(thread_id))
        if snap.next != ("gate",) or not snap.interrupts:
            return None
        interrupt = snap.interrupts[0]
        task: Task = snap.values["task"]
        action: PendingAction = snap.values["pending_actions"][0]
        payload: dict[str, Any] = interrupt.value
        return PendingItem(
            thread_id=thread_id,
            interrupt_id=interrupt.id,
            paused_at=str(snap.created_at or ""),
            task_id=task.id,
            agent=task.agent,
            goal=task.goal,
            action_id=payload["action_id"],
            type=payload["type"],
            target=payload["target"],
            preview=payload["preview"],
            risk_level=payload["risk_level"],
            response_schema=dict(getattr(interrupt, "response_schema", None) or {}),
            blocked_by_policy=self.policy.is_blocked(action),  # logs a WARNING if so
        )

    async def list_pending(self) -> list[PendingItem]:
        """Every waiting action, oldest pause first. A thread that can't be read (a state
        shape from another version, a foreign graph in a shared database) is logged and
        skipped: one bad run must not hide the others."""
        items: list[PendingItem] = []
        for thread_id in await self._thread_ids():
            try:
                item = await self.get(thread_id)
            except Exception:
                log.exception("inbox: can't read run %s; skipped", thread_id)
                continue
            if item is not None:
                items.append(item)
        return sorted(items, key=lambda i: i.paused_at)

    async def decide(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        action_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> Outcome:
        """Answer the interrupt `thread_id` is waiting on and resume the run.

        Raises AlreadyDecided if that interrupt isn't pending any more, and Refused if
        the answer is invalid (wrong action, feedback too long, approving a "never"
        action). After Refused the run is still paused; answer again with a new call.
        """
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        self._lock_users[thread_id] = self._lock_users.get(thread_id, 0) + 1
        try:
            return await self._decide_locked(
                lock, thread_id, interrupt_id, action_id, approved, feedback
            )
        finally:
            self._lock_users[thread_id] -= 1
            if not self._lock_users[thread_id]:
                del self._lock_users[thread_id], self._locks[thread_id]

    async def _decide_locked(
        self,
        lock: asyncio.Lock,
        thread_id: str,
        interrupt_id: str,
        action_id: str,
        approved: bool,
        feedback: str | None,
    ) -> Outcome:
        async with lock:
            item = await self.get(thread_id)
            if item is None or item.interrupt_id != interrupt_id:
                raise AlreadyDecided(f"run {thread_id} is not waiting on interrupt {interrupt_id}")
            text = (feedback or "").strip() or None
            try:
                decision = ApprovalDecision(action_id=action_id, approved=approved, feedback=text)
            except ValidationError as exc:
                raise Refused(_reason(exc)) from exc
            resume: Command[Any] = Command(resume={interrupt_id: decision.model_dump(mode="json")})
            try:
                out = await self.graph.ainvoke(resume, _cfg(thread_id))
            except ValidationError as exc:  # the gate's answer_schema refused it
                raise Refused(_reason(exc)) from exc
            except ActionBlockedError as exc:
                raise Refused(str(exc)) from exc
        log.info(
            "inbox: run %s action %s %s",
            thread_id,
            action_id,
            "approved" if approved else "rejected",
        )
        still_paused = isinstance(out, dict) and bool(out.get("__interrupt__"))
        return Outcome(thread_id=thread_id, approved=approved, still_paused=still_paused)
