"""Approval policy: which action types run automatically and which wait for a human.

The table is config, not code: a TOML file with one row per action type. The
demo table ships with this package; the real one lives in measured-ops-private and
is passed to `load_policy(path)`.

Two rules no table can override (fail closed):
- an action type the table doesn't list needs approval;
- an action an agent marks `risk_level="high"` needs approval.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from measured_ops.contracts import NAME_RE, PendingAction

Decision = Literal["auto", "approval"]

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
        """The gate's answer for one proposed action."""
        if action.risk_level == "high":
            return "approval"
        return self.decide_type(action.type)

    def requires_approval(self, action: PendingAction) -> bool:
        return self.decide(action) == "approval"


def load_policy(path: Path | None = None) -> ApprovalPolicy:
    """Load a policy table. No path = the public demo table bundled with this package."""
    if path is None:
        raw = files("measured_ops").joinpath("config", DEMO_POLICY).read_text(encoding="utf-8")
    else:
        raw = path.read_text(encoding="utf-8")
    return ApprovalPolicy.model_validate(tomllib.loads(raw))
