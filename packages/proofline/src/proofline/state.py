"""Graph state — the single source of truth for what flows between nodes.

Ownership (who writes which field) is documented in docs/ARCHITECTURE.md §3 and
checked by tests/test_contracts.py. Nodes return partial dicts; never mutate state.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from proofline.schemas import (
    Critique,
    Draft,
    FinalScript,
    Plan,
    ResearchNotes,
    RevisionRecord,
    RunConfig,
    VideoFormat,
)


class InputState(TypedDict):
    topic: str
    audience: str
    video_format: VideoFormat
    run_config: RunConfig


class ScriptState(TypedDict, total=False):
    # inputs
    topic: str
    audience: str
    video_format: VideoFormat
    run_config: RunConfig
    # planner
    plan: Plan
    # researcher
    research_notes: ResearchNotes
    # writer
    draft: Draft
    revision_count: int
    # critic
    critique: Critique
    # human_review
    approved: bool
    feedback: str | None
    # finalizer
    final_script: FinalScript
    # append-only audit trail (writer, critic, human_review)
    history: Annotated[list[RevisionRecord], operator.add]


# node name -> fields it may write (history is shared and append-only)
FIELD_OWNERS: dict[str, frozenset[str]] = {
    "planner": frozenset({"plan"}),
    "researcher": frozenset({"research_notes"}),
    "writer": frozenset({"draft", "revision_count", "history"}),
    "critic": frozenset({"critique", "history"}),
    "human_review": frozenset({"approved", "feedback", "history"}),
    "finalizer": frozenset({"final_script"}),
}
