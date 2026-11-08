"""The C1 contract: what flows between the supervisor, agents and the approval gate.

TypedDict for graph state, Pydantic for every value in it (ADR-0002, kept for
Measured Ops by ADR-0014). Every model is frozen and forbids unknown fields.

Flow (supervisor from C2, inbox from C4):

    Task ──agent subgraph──▶ AgentResult(actions=[PendingAction, ...])
                                   │
                    policy.decide(action) ── "auto" ──▶ run
                                   │
                               "approval" ──▶ inbox ──▶ ApprovalDecision
                                                           │
                                     rejected + feedback ──▶ new Task(parent_id=...)

A rejection never loops inside an agent's graph: it becomes a new `Task` for the
same agent, carrying the feedback and pointing at the task it revises (ADR-0014).
"""

from __future__ import annotations

import operator
import re
from datetime import UTC, datetime
from typing import Annotated, Literal, Self, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from measured_evals import EvalScores

# snake_case names: action types ("publish_public") and agent names ("content")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NAME_PATTERN = NAME_RE.pattern

RiskLevel = Literal["low", "medium", "high"]
ResultStatus = Literal["succeeded", "failed"]


def new_id() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Task(_Model):
    """One unit of work for one agent. Frozen but not hashable (dict field): key by `id`."""

    id: str = Field(default_factory=new_id, min_length=1)
    agent: str = Field(pattern=NAME_PATTERN, description="Name of the agent that runs it.")
    goal: str = Field(min_length=1, max_length=2000, description="What done looks like.")
    inputs: dict[str, JsonValue] = Field(
        default_factory=dict, description="Agent-specific inputs, e.g. topic and audience."
    )
    feedback: str | None = Field(
        default=None,
        max_length=4000,
        description="A human's feedback from rejecting an earlier action; set on revisions.",
    )
    parent_id: str | None = Field(
        default=None, description="The task this one revises, when it comes from a rejection."
    )
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _revision_has_a_parent(self) -> Self:
        if self.feedback is not None and self.parent_id is None:
            raise ValueError("a task with feedback revises another task: set parent_id")
        if self.parent_id == self.id:
            raise ValueError("a task can't revise itself")
        return self


class PendingAction(_Model):
    """An action an agent proposes that would change something. The policy decides
    whether it runs automatically or waits for a human in the inbox."""

    id: str = Field(default_factory=new_id, min_length=1)
    task_id: str = Field(min_length=1)
    type: str = Field(
        pattern=NAME_PATTERN,
        description="Action type, a key in the approval policy table, e.g. 'publish_public'.",
    )
    target: str = Field(
        min_length=1,
        max_length=300,
        description="Where the action lands, e.g. 'linkedin' or 'github:owner/repo'.",
    )
    preview: str = Field(
        min_length=1,
        max_length=20_000,
        description="What the human sees before approving: the text, diff or summary.",
    )
    eval_scores: EvalScores | None = Field(
        default=None,
        description=(
            "Scores from measured_evals, written only by the eval node (C7), never by the "
            "proposing agent: run_agent rejects actions that arrive with scores (ADR-0008)."
        ),
    )
    risk_level: RiskLevel = Field(
        description="The agent's risk call. 'high' always needs approval, whatever the policy."
    )
    created_at: datetime = Field(default_factory=_now)


class AgentResult(_Model):
    """What an agent's subgraph returns for one Task."""

    task_id: str = Field(min_length=1)
    agent: str = Field(pattern=NAME_PATTERN)
    status: ResultStatus
    summary: str = Field(max_length=4000, description="One paragraph on what was done.")
    actions: tuple[PendingAction, ...] = Field(
        default=(), description="Actions proposed for the supervisor to gate. Never executed here."
    )
    error: str | None = Field(default=None, max_length=4000)
    finished_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "failed" and not self.error:
            raise ValueError("a failed result must say why in `error`")
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("a succeeded result has no `error`")
        if self.status == "failed" and self.actions:
            raise ValueError("a failed result proposes no actions")
        stray = sorted({a.task_id for a in self.actions} - {self.task_id})
        if stray:
            raise ValueError(f"actions belong to other tasks: {stray}")
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("action ids must be unique within a result")
        return self


class ApprovalDecision(_Model):
    """A human's answer to one PendingAction: the approval gate's resume value (C2)."""

    action_id: str = Field(min_length=1)
    approved: bool
    feedback: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional. On a rejection with feedback, the supervisor opens a revision Task.",
    )
    decided_at: datetime = Field(default_factory=_now)


class OpsState(TypedDict, total=False):
    """The supervisor graph's state (wired in C2). Values are the models above."""

    task: Task  # the task in flight
    results: Annotated[list[AgentResult], operator.add]  # one per agent run, append-only
    # waiting at the gate. No reducer: last write wins, so C2 must give it one owner
    # (or a reducer) before agents can run in parallel.
    pending_actions: list[PendingAction]
    decisions: Annotated[list[ApprovalDecision], operator.add]  # append-only audit trail


def initial_ops_state(task: Task) -> OpsState:
    """A new supervisor run: the task plus a default for every field."""
    return OpsState(task=task, results=[], pending_actions=[], decisions=[])


# Every type that can appear in OpsState. The checkpointer's serde allowlist is
# built from this tuple (ADR-0006); tests/test_ops_contracts.py checks it is complete.
STATE_TYPES: tuple[type, ...] = (Task, AgentResult, PendingAction, ApprovalDecision, EvalScores)
SERDE_ALLOWLIST: list[tuple[str, str]] = [(t.__module__, t.__qualname__) for t in STATE_TYPES]
