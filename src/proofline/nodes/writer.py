"""writer — plan + notes (+ critique/feedback) → Draft. Week 1 S4 (first draft), Week 2 S2 (revisions).

Contract (docs/ARCHITECTURE.md §4):
    reads:   topic, audience, video_format, run_config, plan, research_notes,
             draft?, critique?, approved?, feedback?, revision_count?
    writes:  draft, revision_count, history
    LLM:     prompts/writer.md, role="writer", schema=Draft
Steps:
    1. is_revision = "draft" in state
       revision_count = state.get("revision_count", 0) + 1 if is_revision else 0
    2. rejected_by_human = state.get("approved") is False
       ctx = render.revision_context(state.get("draft"), state.get("critique"),
                                     state.get("feedback"), rejected_by_human)
    3. brand_voice = settings.brand_voice_path.read_text()
       target_seconds = plan.target_seconds; target_words = round(target_seconds / 60 * WORDS_PER_MINUTE)
    4. Render + generate Draft.
    5. Keep only cited_source_ids that exist in research_notes; log WARNING for dropped ids.
       (Draft is frozen → use draft.model_copy(update={...}).)
    6. return {"draft": draft, "revision_count": revision_count,
               "history": [RevisionRecord(kind="draft", revision=revision_count,
                                          note="human feedback" if rejected_by_human else "")]}
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.state import ScriptState


async def writer(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    raise NotImplementedError("Week 1 S4 — see module docstring")
