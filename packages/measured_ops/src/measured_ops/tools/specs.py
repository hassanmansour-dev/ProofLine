"""Executable action types (C5, ADR-0022): their params, target and preview. Pure: no
tool calls, no I/O, no MCP import, so agents, the supervisor and tests use them freely.

An agent proposes an executable action with `spec.propose(task_id, params)`: the target
and the preview are rendered here from the params. The supervisor's eval node re-checks
every executable action with `spec.check(action)`, so an agent can't show the human one
thing and have the executor run another: what's approved is exactly what runs.

Two types in C5, both from docs/PLAN.md:
- `triage_ticket` (policy: auto in the demo): move a Jira issue to a status, add or
  remove labels, set its priority. Only issues of the configured projects.
- `schedule_calendar_block` (policy: approval, per C3): put one block on a configured
  calendar. Personal calendars only: the config holds aliases for them, never work ones.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from measured_ops.agent import AgentContractError
from measured_ops.contracts import NAME_PATTERN, PendingAction, RiskLevel
from measured_ops.tools.errors import ScopeError

ISSUE_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]{1,9})-[1-9][0-9]{0,8}$")
LABEL_PATTERN = r"^[A-Za-z0-9_.:-]{1,50}$"
ONE_LINE = r"^[^\r\n]+$"
MAX_BLOCK = timedelta(hours=12)

APPROVE_DRAFT = "approve_draft"  # never executed (ADR-0019): no spec, no executor, ever


@dataclass(frozen=True)
class ToolScope:
    """What the executors may touch: Jira project keys and calendar aliases (from config)."""

    jira_projects: frozenset[str] = frozenset()
    calendars: frozenset[str] = frozenset()


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TriageParams(_Params):
    """A ticket move and/or label and priority changes on one issue."""

    issue: str = Field(pattern=ISSUE_KEY_RE.pattern, description="Issue key, e.g. DEMO-3.")
    to_status: str | None = Field(default=None, max_length=60, pattern=ONE_LINE)
    add_labels: tuple[str, ...] = Field(default=(), max_length=10)
    remove_labels: tuple[str, ...] = Field(default=(), max_length=10)
    priority: str | None = Field(default=None, max_length=60, pattern=ONE_LINE)
    reason: str = Field(min_length=1, max_length=300, description="Why, for the human.")

    @model_validator(mode="after")
    def _a_real_change(self) -> Self:
        if not (self.to_status or self.add_labels or self.remove_labels or self.priority):
            raise ValueError("a triage changes something: status, labels or priority")
        bad = [x for x in (*self.add_labels, *self.remove_labels) if not re.match(LABEL_PATTERN, x)]
        if bad:
            raise ValueError(f"labels are 1-50 letters, digits or _.:- (got {len(bad)} invalid)")
        both = set(self.add_labels) & set(self.remove_labels)
        if both:
            raise ValueError("a label can't be both added and removed")
        return self

    @property
    def project(self) -> str:
        match = ISSUE_KEY_RE.match(self.issue)
        assert match is not None  # the field pattern guarantees it
        return match.group(1)


class BlockParams(_Params):
    """One time block on one configured calendar."""

    calendar: str = Field(pattern=NAME_PATTERN, description="Calendar alias from the config.")
    title: str = Field(min_length=1, max_length=120, pattern=ONE_LINE)
    start: AwareDatetime
    end: AwareDatetime
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _sane_span(self) -> Self:
        if self.end <= self.start:
            raise ValueError("a block ends after it starts")
        if self.end - self.start > MAX_BLOCK:
            raise ValueError(f"a block is at most {MAX_BLOCK.seconds // 3600} hours")
        return self


class ActionSpec[P: _Params](ABC):
    """One executable action type: how its params become a target and a preview."""

    type: str
    params_model: builtins.type[P]

    def parse(self, params: Mapping[str, Any]) -> P:
        return self.params_model.model_validate(dict(params))

    @abstractmethod
    def target(self, params: P) -> str: ...

    @abstractmethod
    def preview(self, params: P) -> str: ...

    @abstractmethod
    def identity(self, params: P) -> dict[str, Any]:
        """What the action changes, without the explanation: two proposals with the same
        identity are the same change (a revision re-proposing an approved action)."""

    @abstractmethod
    def check_scope(self, params: P, scope: ToolScope) -> None:
        """Raise ScopeError if the action touches anything outside `scope`."""

    def key(self, params: P) -> str:
        blob = json.dumps({"type": self.type, **self.identity(params)}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def propose(
        self, task_id: str, params: P, *, risk_level: RiskLevel = "medium"
    ) -> PendingAction:
        """The PendingAction for `params`, with its target and preview rendered here."""
        return PendingAction(
            task_id=task_id,
            type=self.type,
            target=self.target(params),
            preview=self.preview(params),
            params=params.model_dump(mode="json"),
            risk_level=risk_level,
        )

    def check(self, action: PendingAction) -> P:
        """The action's params, if its target and preview are the ones they render to.
        Raises AgentContractError otherwise: the human would approve something else."""
        if action.type != self.type:
            raise AgentContractError(f"action {action.id} is {action.type}, not {self.type}")
        try:
            params = self.parse(action.params)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            raise AgentContractError(
                f"action {action.id} ({self.type}) has invalid params: {_fields(exc)}"
            ) from exc
        if action.target != self.target(params) or action.preview != self.preview(params):
            raise AgentContractError(
                f"action {action.id} ({self.type}): its target or preview doesn't match its "
                "params; propose it with the action spec so the human sees what runs"
            )
        return params


def _fields(exc: ValueError) -> str:
    """Which fields failed, and how, without the values (they may be personal)."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return "invalid"
    return "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', ())) or 'params'}: {e.get('type', 'invalid')}"
        for e in errors()
    )


class TriageSpec(ActionSpec[TriageParams]):
    type = "triage_ticket"
    params_model = TriageParams

    def target(self, params: TriageParams) -> str:
        return f"jira:{params.issue}"

    def preview(self, params: TriageParams) -> str:
        lines = [f"Ticket triage: {params.issue}"]
        if params.to_status:
            lines.append(f"- Move to: {params.to_status}")
        if params.add_labels:
            lines.append(f"- Add labels: {', '.join(params.add_labels)}")
        if params.remove_labels:
            lines.append(f"- Remove labels: {', '.join(params.remove_labels)}")
        if params.priority:
            lines.append(f"- Priority: {params.priority}")
        lines.append(f"Why: {params.reason}")
        return "\n".join(lines)

    def identity(self, params: TriageParams) -> dict[str, Any]:
        return {
            "issue": params.issue,
            "to_status": params.to_status,
            "add_labels": sorted(params.add_labels),
            "remove_labels": sorted(params.remove_labels),
            "priority": params.priority,
        }

    def check_scope(self, params: TriageParams, scope: ToolScope) -> None:
        if params.project not in scope.jira_projects:
            raise ScopeError(f"{params.issue} is not in a configured Jira project")


class CalendarBlockSpec(ActionSpec[BlockParams]):
    type = "schedule_calendar_block"
    params_model = BlockParams

    def target(self, params: BlockParams) -> str:
        return f"calendar:{params.calendar}"

    def preview(self, params: BlockParams) -> str:
        minutes = int((params.end - params.start).total_seconds() // 60)
        span = f"{minutes // 60} h {minutes % 60:02d} min"
        lines = [
            f"Calendar block: {params.calendar}",
            f"- What: {params.title}",
            f"- Start: {params.start.isoformat()}",
            f"- End: {params.end.isoformat()} ({span})",
        ]
        if params.description:
            lines.append(f"- Notes: {params.description}")
        return "\n".join(lines)

    def identity(self, params: BlockParams) -> dict[str, Any]:
        return {
            "calendar": params.calendar,
            "title": params.title,
            "start": params.start.astimezone(UTC).isoformat(),
            "end": params.end.astimezone(UTC).isoformat(),
        }

    def check_scope(self, params: BlockParams, scope: ToolScope) -> None:
        if params.calendar not in scope.calendars:
            raise ScopeError(f"calendar {params.calendar!r} is not in the config")


TRIAGE = TriageSpec()
CALENDAR_BLOCK = CalendarBlockSpec()

# Every executable type. A type here never runs without a configured executor (the
# supervisor fails loudly); a type not here is never executed ("manual" outcome).
SPECS: dict[str, ActionSpec[Any]] = {TRIAGE.type: TRIAGE, CALENDAR_BLOCK.type: CALENDAR_BLOCK}
assert APPROVE_DRAFT not in SPECS  # ADR-0019
