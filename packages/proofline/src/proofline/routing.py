"""Pure routing functions (ADR-0004). No I/O, no settings, no LLM — state in, edge out."""

from __future__ import annotations

from typing import Literal

from proofline.state import ScriptState

AfterCritic = Literal["writer", "finalizer"]


def route_after_critic(state: ScriptState) -> AfterCritic:
    """Revise while below threshold and budget remains; otherwise finish.

    Human approval is not part of this graph: the supervisor gates the content
    agent's proposed actions (ADR-0014).
    """
    cfg = state["run_config"]
    score = state.get("score")
    if score is None:
        raise ValueError("route_after_critic: no score in state; the critic sets it")
    if score >= cfg.score_threshold:
        return "finalizer"
    if state.get("revision_count", 0) >= cfg.max_revisions:
        return "finalizer"
    return "writer"
