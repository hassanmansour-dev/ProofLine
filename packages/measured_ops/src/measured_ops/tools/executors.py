"""Executors (C5, ADR-0022): run `ready` actions through the write ports.

The supervisor's `execute` node calls `execute_action` once per ready action and records
the `ActionOutcome`. Rules:

- **Only executable types run** (`specs.SPECS`). Any other type gets a "manual" outcome:
  nothing runs, a human does it. `approve_draft` is one of them, now and later (ADR-0019):
  `Executors` refuses an executor for it.
- **An executable type never runs without its executor.** If the tools aren't configured,
  `execute_action` raises `ToolConfigError`: an approved ticket move is never silently
  dropped as "manual".
- **Re-checked before it runs**: params, target and preview (`spec.check`), then scope
  (configured projects and calendars). A mismatch or a scope breach raises.
- **Idempotent.** A change already applied in this run (same identity key: a revision
  re-proposing an approved action) is "already_applied" without a tool call. Otherwise each
  executor reads first and writes only what differs, so a retry after a crash between the
  write and the checkpoint finds the change in place and records "already_applied".
- **Verified.** The triage executor re-reads the issue after writing and raises if the
  change didn't take.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from measured_ops.contracts import ActionOutcome, PendingAction
from measured_ops.tools.errors import ToolConfigError, ToolError
from measured_ops.tools.ports import (
    CalendarReads,
    CalendarWrites,
    JiraIssue,
    JiraReads,
    JiraWrites,
)
from measured_ops.tools.specs import (
    APPROVE_DRAFT,
    CALENDAR_BLOCK,
    SPECS,
    TRIAGE,
    ActionSpec,
    BlockParams,
    ToolScope,
    TriageParams,
)

log = logging.getLogger("measured_ops.tools")


@dataclass(frozen=True)
class Execution:
    status: Literal["applied", "already_applied"]
    detail: str
    ref: str | None = None


class Executor(Protocol):
    """Runs one executable action type. `spec.type` is the type it runs."""

    @property
    def spec(self) -> ActionSpec[Any]: ...

    async def run(self, params: Any) -> Execution: ...


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").casefold() == (b or "").casefold()


def _triage_done(issue: JiraIssue, p: TriageParams) -> bool:
    labels = set(issue.labels)
    return (
        (p.to_status is None or _same(issue.status, p.to_status))
        and set(p.add_labels) <= labels
        and not set(p.remove_labels) & labels
        and (p.priority is None or _same(issue.priority, p.priority))
    )


class TriageExecutor:
    """`triage_ticket`: transition, labels, priority. Reads first, writes what differs,
    then re-reads to check it took."""

    spec: ActionSpec[Any] = TRIAGE

    def __init__(self, reads: JiraReads, writes: JiraWrites) -> None:
        self.reads = reads
        self.writes = writes

    async def run(self, params: Any) -> Execution:
        p: TriageParams = params
        issue = await self.reads.get_issue(p.issue)
        if issue.key != p.issue:
            raise ToolError(f"asked for {p.issue}, the tool returned another issue")
        if _triage_done(issue, p):
            return Execution("already_applied", f"{p.issue}: already as asked")
        changes: list[str] = []
        if p.to_status and not _same(issue.status, p.to_status):
            options = await self.reads.transitions(p.issue)
            match = [t for t in options if _same(t.to_status, p.to_status)]
            if not match:
                raise ToolError(
                    f"{p.issue}: no transition from {issue.status!r} to {p.to_status!r}"
                )
            await self.writes.transition(p.issue, match[0].id)
            changes.append(f"moved to {p.to_status}")
        labels = set(issue.labels)
        new_labels = (labels | set(p.add_labels)) - set(p.remove_labels)
        set_priority = p.priority if p.priority and not _same(issue.priority, p.priority) else None
        if new_labels != labels or set_priority:
            await self.writes.update(
                p.issue,
                labels=sorted(new_labels) if new_labels != labels else None,
                priority=set_priority,
            )
            if new_labels != labels:
                added = sorted(new_labels - labels)
                removed = sorted(labels - new_labels)
                changes.append(
                    "labels " + " ".join([*(f"+{x}" for x in added), *(f"-{x}" for x in removed)])
                )
            if set_priority:
                changes.append(f"priority {set_priority}")
        after = await self.reads.get_issue(p.issue)
        if not _triage_done(after, p):
            raise ToolError(f"{p.issue}: the triage didn't take effect")
        return Execution("applied", f"{p.issue}: {'; '.join(changes)}")


class CalendarBlockExecutor:
    """`schedule_calendar_block`: creates the block unless an event with the same title,
    start and end is already on that calendar."""

    spec: ActionSpec[Any] = CALENDAR_BLOCK

    def __init__(self, reads: CalendarReads, writes: CalendarWrites) -> None:
        self.reads = reads
        self.writes = writes

    async def run(self, params: Any) -> Execution:
        p: BlockParams = params
        for event in await self.reads.list_events(p.calendar, p.start, p.end):
            if event.title == p.title and event.start == p.start and event.end == p.end:
                return Execution("already_applied", "block already on the calendar", event.id)
        event_id = await self.writes.create_event(
            p.calendar, title=p.title, start=p.start, end=p.end, description=p.description
        )
        return Execution("applied", f"block added to calendar {p.calendar}", event_id)


class Executors:
    """The executors for this deployment, and the scope they may touch."""

    def __init__(self, executors: Iterable[Executor], scope: ToolScope) -> None:
        self.scope = scope
        self._by_type: dict[str, Executor] = {}
        for executor in executors:
            kind = executor.spec.type
            if kind == APPROVE_DRAFT:
                raise ValueError("approve_draft never gets an executor (ADR-0019)")
            if kind not in SPECS or SPECS[kind] is not executor.spec:
                raise ValueError(f"{kind!r} is not an executable action type")
            if kind in self._by_type:
                raise ValueError(f"two executors for {kind!r}")
            self._by_type[kind] = executor

    @property
    def types(self) -> frozenset[str]:
        return frozenset(self._by_type)

    def check(self, action: PendingAction) -> Any:
        """Params, target, preview and scope; returns the params. Raises
        AgentContractError, ScopeError, or ToolConfigError (no executor for the type)."""
        spec = SPECS[action.type]
        params = spec.check(action)
        if action.type not in self._by_type:
            raise ToolConfigError(f"no executor configured for {action.type}")
        spec.check_scope(params, self.scope)
        return params

    async def run(self, action: PendingAction, done: Sequence[ActionOutcome]) -> ActionOutcome:
        params = self.check(action)
        spec = SPECS[action.type]
        key = spec.key(params)
        for prior in done:
            if prior.key == key and prior.status in ("applied", "already_applied"):
                return ActionOutcome(
                    action_id=action.id,
                    type=action.type,
                    status="already_applied",
                    key=key,
                    detail=f"same change as action {prior.action_id}, already applied",
                    ref=prior.ref,
                )
        result = await self._by_type[action.type].run(params)
        return ActionOutcome(
            action_id=action.id,
            type=action.type,
            status=result.status,
            key=key,
            detail=result.detail[:500],
            ref=result.ref,
        )


def manual_outcome(action: PendingAction) -> ActionOutcome:
    detail = (
        "approved draft: pasted by hand, never executed"
        if action.type == APPROVE_DRAFT
        else "no executor for this action type: nothing ran, a human does it"
    )
    return ActionOutcome(action_id=action.id, type=action.type, status="manual", detail=detail)


async def execute_action(
    action: PendingAction, executors: Executors | None, done: Sequence[ActionOutcome]
) -> ActionOutcome:
    """The outcome of one ready action. Raises (and so stops the run) on any failure."""
    if action.type not in SPECS:
        return manual_outcome(action)
    if executors is None:
        raise ToolConfigError(
            f"action {action.id} is {action.type}, which only runs through a configured "
            "executor, and no tools are configured (MEASURED_OPS_TOOLS_CONFIG)"
        )
    outcome = await executors.run(action, done)
    # ids, type and status only: details can name calendars, targets can be personal
    log.info("tools: action %s (%s) %s", action.id, action.type, outcome.status)
    return outcome
