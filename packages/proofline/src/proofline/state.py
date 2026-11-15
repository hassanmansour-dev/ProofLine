"""Graph state — the single source of truth for what flows between nodes.

Ownership (who writes which field) is documented in docs/ARCHITECTURE.md §3 and
checked by tests/test_contracts.py. Nodes return partial dicts; never mutate state.

TypedDict for the graph, Pydantic for every value (ADR-0002). Defaults live in
`initial_state()`, so every run starts with every field present.
"""

from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

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
    # a human's feedback on an earlier draft; set when a rejected action comes back
    # as a revision Task (ADR-0014). Nothing in this graph writes it.
    feedback: NotRequired[str | None]
    # the rejected action's preview, carried by the revision Task (ADR-0019): what the
    # writer revises from when there's no draft in state yet. Nothing here writes it.
    previous_preview: NotRequired[str | None]


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
    # critic (C8)
    critique: Critique | None
    score: float | None
    feedback: str | None  # input, see InputState
    previous_preview: str | None  # input, see InputState
    # finalizer
    final_script: FinalScript
    # append-only audit trail (writer, critic)
    history: Annotated[list[RevisionRecord], operator.add]


def initial_state(
    topic: str,
    audience: str,
    run_config: RunConfig,
    *,
    feedback: str | None = None,
    previous_preview: str | None = None,
) -> ScriptState:
    """A new run's state: the inputs plus a default for every field later steps use.
    `feedback` and `previous_preview` come from a revision Task (ADR-0019)."""
    return ScriptState(
        topic=topic,
        audience=audience,
        run_config=run_config,
        revision_count=0,
        critique=None,
        score=None,
        feedback=feedback,
        previous_preview=previous_preview,
        history=[],
    )


# node name -> fields it may write (history is shared and append-only)
FIELD_OWNERS: dict[str, frozenset[str]] = {
    "planner": frozenset({"outline"}),
    "researcher": frozenset({"research_notes"}),
    "writer": frozenset({"draft", "revision_count", "history"}),
    "critic": frozenset({"critique", "score", "history"}),
    "finalizer": frozenset({"final_script"}),
}
