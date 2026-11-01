"""Graph state — the single source of truth for what flows between nodes.

Ownership (who writes which field) is documented in docs/ARCHITECTURE.md §3 and
checked by tests/test_contracts.py. Nodes return partial dicts; never mutate state.

TypedDict for the graph, Pydantic for every value (ADR-0002). Defaults live in
`initial_state()`, so every run starts with every field present.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from proofline.schemas import (
    Critique,
    Draft,
    FinalScript,
    Outline,
    ResearchNotes,
    RevisionRecord,
    RunConfig,
)


class InputState(TypedDict):
    topic: str
    audience: str
    run_config: RunConfig


class ScriptState(TypedDict, total=False):
    # inputs
    topic: str
    audience: str
    run_config: RunConfig  # settings snapshot, so a resumed run behaves like it started (ADR-0004)
    # planner
    outline: Outline
    # researcher
    research_notes: ResearchNotes
    # writer
    draft: Draft
    revision_count: int
    # critic (Week 2)
    critique: Critique | None
    score: float | None
    # human_review (Week 3)
    approved: bool | None
    feedback: str | None
    # finalizer
    final_script: FinalScript
    # append-only audit trail (writer, critic, human_review)
    history: Annotated[list[RevisionRecord], operator.add]


def initial_state(topic: str, audience: str, run_config: RunConfig) -> ScriptState:
    """A new run's state: the inputs plus a default for every field later weeks use."""
    return ScriptState(
        topic=topic,
        audience=audience,
        run_config=run_config,
        revision_count=0,
        critique=None,
        score=None,
        approved=None,  # None = not reviewed yet; False = rejected
        feedback=None,
        history=[],
    )


# node name -> fields it may write (history is shared and append-only)
FIELD_OWNERS: dict[str, frozenset[str]] = {
    "planner": frozenset({"outline"}),
    "researcher": frozenset({"research_notes"}),
    "writer": frozenset({"draft", "revision_count", "history"}),
    "critic": frozenset({"critique", "score", "history"}),
    "human_review": frozenset({"approved", "feedback", "history"}),
    "finalizer": frozenset({"final_script"}),
}
