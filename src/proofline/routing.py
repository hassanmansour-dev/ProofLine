"""Pure routing functions (ADR-0004). No I/O, no settings, no LLM — state in, edge out."""

from __future__ import annotations

from typing import Literal

from proofline.state import ScriptState

AfterCritic = Literal["writer", "human_review"]
AfterHuman = Literal["writer", "finalizer"]


def route_after_critic(state: ScriptState) -> AfterCritic:
    """Revise while below threshold and budget remains; otherwise hand to the human."""
    cfg = state["run_config"]
    if state["critique"].overall >= cfg.score_threshold:
        return "human_review"
    if state.get("revision_count", 0) >= cfg.max_revisions:
        return "human_review"
    return "writer"


def route_after_human(state: ScriptState) -> AfterHuman:
    return "finalizer" if state.get("approved") is True else "writer"
