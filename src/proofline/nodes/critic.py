"""critic — Draft → Critique. Week 1 S4 (score only), Week 2 (drives the loop).

Contract (docs/ARCHITECTURE.md §4, ADR-0008):
    reads:   audience, video_format, run_config, plan, research_notes, draft, revision_count
    writes:  critique, history
    LLM:     prompts/critic.md, role="critic", schema=Critique (temperature 0 via RunConfig)
Steps:
    1. rubric = settings.rubric_path.read_text(); brand_voice = settings.brand_voice_path.read_text()
    2. Render with plan_block, notes_block, draft_block; estimated_seconds = draft.estimated_seconds
    3. critique = await llm.generate(..., Critique, role="critic", ...)
       The overall score is critique.overall (computed in Python, with the accuracy gate).
    4. Validate all 8 criteria present exactly once; if not, raise ValueError (the adapter's
       schema enforces count, this enforces uniqueness).
    5. log INFO: revision, overall, lowest criterion
    6. return {"critique": critique,
               "history": [RevisionRecord(kind="critique", revision=state.get("revision_count", 0),
                                          score=critique.overall)]}
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.state import ScriptState


async def critic(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    raise NotImplementedError("Week 1 S4 — see module docstring")
