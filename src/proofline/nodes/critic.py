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

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import load_prompt
from proofline.render import draft_block, notes_block, plan_block
from proofline.schemas import CRITERION_WEIGHTS, Critique, RevisionRecord
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.critic")


async def critic(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    started = time.perf_counter()
    settings = runtime.context.settings
    plan = state["plan"]
    draft = state["draft"]
    revision = state.get("revision_count", 0)

    rubric, brand_voice = await asyncio.gather(
        asyncio.to_thread(settings.rubric_path.read_text, "utf-8"),
        asyncio.to_thread(settings.brand_voice_path.read_text, "utf-8"),
    )
    prompt = load_prompt("critic").render(
        rubric=rubric,
        brand_voice=brand_voice,
        audience=state["audience"],
        video_format=state["video_format"],
        target_seconds=plan.target_seconds,
        estimated_seconds=draft.estimated_seconds,
        plan=plan_block(plan),
        research_notes=notes_block(state["research_notes"]),
        draft=draft_block(draft),
    )
    critique = await runtime.context.llm.generate(
        prompt, Critique, role="critic", run_config=state["run_config"]
    )

    # The schema enforces 8 scores; this enforces one per criterion, so overall is well-defined.
    counts = Counter(s.criterion for s in critique.scores)
    if set(counts) != set(CRITERION_WEIGHTS) or any(n != 1 for n in counts.values()):
        missing = sorted(set(CRITERION_WEIGHTS) - set(counts))
        repeated = sorted(c for c, n in counts.items() if n > 1)
        raise ValueError(
            f"critic: each criterion must be scored once; missing={missing} repeated={repeated}"
        )

    lowest = min(critique.scores, key=lambda s: s.score)
    log.info(
        "critic: revision %d, overall %.1f, lowest %s=%d (%.1fs)",
        revision,
        critique.overall,
        lowest.criterion,
        lowest.score,
        time.perf_counter() - started,
    )
    return {
        "critique": critique,
        "history": [RevisionRecord(kind="critique", revision=revision, score=critique.overall)],
    }
