"""human_review — interrupt for approve/reject. Week 3 S1 (ADR-0005).

Contract (docs/ARCHITECTURE.md §4):
    reads:   draft, critique, revision_count
    writes:  approved, feedback, history
!! This node RE-RUNS FROM THE TOP when resumed. Nothing with side effects before
!! interrupt(): no logging of "review requested", no file writes, no LLM calls.
Steps:
    1. payload = {"title": draft.title, "draft": render.draft_block(draft),
                  "score": critique.overall, "must_fix": critique.must_fix,
                  "revision_count": revision_count}
    2. raw = interrupt(payload)
    3. decision = HumanDecision.model_validate(raw)
    4. return {"approved": decision.approved, "feedback": decision.feedback,
               "history": [RevisionRecord(kind="human", revision=revision_count,
                                          note="approved" if decision.approved else (decision.feedback or "rejected"))]}
Sync def is fine here (no I/O).
"""

from __future__ import annotations

from typing import Any

from proofline.state import ScriptState


def human_review(state: ScriptState) -> dict[str, Any]:
    raise NotImplementedError("Week 3 S1 — see module docstring")
