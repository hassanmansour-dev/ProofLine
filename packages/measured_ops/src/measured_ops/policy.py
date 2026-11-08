"""Approval policy: which action types run automatically, which wait for a human,
and which are never allowed.

The table is config, not code: a TOML file with one row per action type. The
demo table ships with this package; the real one lives in measured-ops-private and
is passed to `load_policy(path)`.

Three tiers: "auto" runs without asking; "approval" waits in the inbox;
"never" is blocked and logged, never reaches the inbox, and no approval unblocks it.

Rules no table can override (fail closed):
- an action type the table doesn't list needs approval;
- an action an agent marks `risk_level="high"` needs approval, unless its row is
  "never": risk can raise "auto" to "approval" but never lowers "never".
"""

from __future__ import annotations

import logging
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from measured_ops.contracts import NAME_RE, ApprovalDecision, PendingAction

Decision = Literal["auto", "approval", "never"]

log = logging.getLogger("measured_ops.policy")

DEMO_POLICY = "approval_policy.demo.toml"


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    actions: dict[str, Decision] = Field(default_factory=dict)

    @field_validator("actions")
    @classmethod
    def _snake_case_types(cls, actions: dict[str, Decision]) -> dict[str, Decision]:
        bad = sorted(t for t in actions if not NAME_RE.match(t))
        if bad:
            raise ValueError(f"action types must be snake_case: {bad}")
        return actions

    def decide_type(self, action_type: str) -> Decision:
        """The table's answer for an action type. Unknown types need approval."""
        return self.actions.get(action_type, "approval")

    def decide(self, action: PendingAction) -> Decision:
        """The gate's answer for one proposed action. Logs every "never" at WARNING."""
        decision = self.decide_type(action.type)
        if decision == "never":
            log.warning(
                "policy: blocked action %s (type=%s, target=%s, task=%s): "
                "this action type is never allowed",
                action.id,
                action.type,
                action.target,
                action.task_id,
            )
            return "never"
        if action.risk_level == "high":
            return "approval"
        return decision

    def requires_approval(self, action: PendingAction) -> bool:
        return self.decide(action) == "approval"

    def is_blocked(self, action: PendingAction) -> bool:
        return self.decide(action) == "never"

    def check_decision(self, action: PendingAction, decision: ApprovalDecision) -> None:
        """Refuse a human decision that would let a blocked action through.

        The gate (C2) and inbox (C4) call this before acting on any decision, so
        not even a forged or mistaken approval can unblock a "never" action.
        """
        if decision.action_id != action.id:
            raise ValueError(f"decision is for action {decision.action_id}, not {action.id}")
        if decision.approved and self.is_blocked(action):
            raise ActionBlockedError(action)


class ActionBlockedError(PermissionError):
    """An approval was given for an action the policy never allows."""

    def __init__(self, action: PendingAction) -> None:
        super().__init__(
            f"action {action.id} ({action.type}) is never allowed; no approval can unblock it"
        )
        self.action = action


def load_policy(path: Path | None = None) -> ApprovalPolicy:
    """Load a policy table. No path = the public demo table bundled with this package."""
    if path is None:
        raw = files("measured_ops").joinpath("config", DEMO_POLICY).read_text(encoding="utf-8")
    else:
        raw = path.read_text(encoding="utf-8")
    return ApprovalPolicy.model_validate(tomllib.loads(raw))
