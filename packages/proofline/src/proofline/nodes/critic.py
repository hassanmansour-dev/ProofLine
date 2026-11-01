"""critic — Draft → Critique + score. Not wired until Week 2 (critic loop).

Contract (docs/ARCHITECTURE.md §4, ADR-0008):
    reads:   audience, run_config, outline, research_notes, draft, revision_count
    writes:  critique, score, history
    LLM:     prompts/critic.md, role="critic", schema=Critique (temperature 0 via RunConfig)
Notes:
    score = critique.overall, computed in Python with the accuracy gate, never the model's.
    Critique's validator enforces each criterion exactly once, so a bad output goes
    through the adapter's repair retry (ADR-0011).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import load_prompt
from proofline.render import draft_block, notes_block, outline_block
from proofline.schemas import Critique, RevisionRecord
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.critic")


async def critic(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    started = time.perf_counter()
    settings = runtime.context.settings
    cfg = state["run_config"]
    draft = state["draft"]
    revision = state.get("revision_count", 0)

    rubric, brand_voice = await asyncio.gather(
        asyncio.to_thread(settings.rubric_path.read_text, "utf-8"),
        asyncio.to_thread(settings.brand_voice_path.read_text, "utf-8"),
    )
    prompt = load_prompt("critic").render(
        brand=cfg.brand_name,
        rubric=rubric,
        brand_voice=brand_voice,
        audience=state["audience"],
        word_count=draft.word_count,
        max_words=cfg.max_words,
        outline=outline_block(state["outline"]),
        research_notes=notes_block(state["research_notes"]),
        draft=draft_block(draft),
    )
    critique = await runtime.context.llm.generate(prompt, Critique, role="critic", run_config=cfg)

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
        "score": critique.overall,
        "history": [RevisionRecord(kind="critique", revision=revision, score=critique.overall)],
    }
