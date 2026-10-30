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
    5. Citations live only in the narration (Draft.cited_source_ids is parsed from it, ADR-0011).
       Strip inline citations to ids not in research_notes; log WARNING for dropped ids.
       (Draft is frozen → use draft.model_copy(update={...}).)
    6. return {"draft": draft, "revision_count": revision_count,
               "history": [RevisionRecord(kind="draft", revision=revision_count,
                                          note="human feedback" if rejected_by_human else "")]}
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import load_prompt
from proofline.render import notes_block, plan_block, revision_context
from proofline.schemas import CITATION_RE, WORDS_PER_MINUTE, Draft, RevisionRecord
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.writer")


# A citation plus the spaces before it, so dropping "claim [S9]." leaves "claim."
_CITATION_WITH_SPACE_RE = re.compile(r"(?P<space>[ \t]*)" + CITATION_RE.pattern)


def _strip_unknown_citations(text: str, known: set[str], dropped: list[str]) -> str:
    def keep_known(m: re.Match[str]) -> str:
        ids = [i.strip() for i in m.group(2).split(",")]
        kept = [i for i in ids if i in known]
        if len(kept) == len(ids):
            return m.group(0)
        dropped.extend(i for i in ids if i not in known)
        return m.group("space") + "".join(f"[{i}]" for i in kept) if kept else ""

    return _CITATION_WITH_SPACE_RE.sub(keep_known, text)


def _without_unknown_citations(draft: Draft, known: set[str]) -> tuple[Draft, list[str]]:
    """Remove inline citations to ids the researcher never assigned (ADR-0007)."""
    dropped: list[str] = []
    update = {
        "hook": _strip_unknown_citations(draft.hook, known, dropped),
        "call_to_action": _strip_unknown_citations(draft.call_to_action, known, dropped),
        "sections": [
            s.model_copy(
                update={"narration": _strip_unknown_citations(s.narration, known, dropped)}
            )
            for s in draft.sections
        ],
    }
    return (draft.model_copy(update=update) if dropped else draft), dropped


async def writer(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    started = time.perf_counter()
    deps = runtime.context
    plan = state["plan"]
    notes = state["research_notes"]

    is_revision = "draft" in state
    revision_count = state.get("revision_count", 0) + 1 if is_revision else 0
    rejected_by_human = state.get("approved") is False
    ctx = revision_context(
        state.get("draft"), state.get("critique"), state.get("feedback"), rejected_by_human
    )
    brand_voice = await asyncio.to_thread(deps.settings.brand_voice_path.read_text, "utf-8")

    prompt = load_prompt("writer").render(
        brand_voice=brand_voice,
        topic=state["topic"],
        audience=state["audience"],
        video_format=state["video_format"],
        target_seconds=plan.target_seconds,
        target_words=round(plan.target_seconds / 60 * WORDS_PER_MINUTE),
        plan=plan_block(plan),
        research_notes=notes_block(notes),
        revision_context=ctx,
    )
    draft = await deps.llm.generate(prompt, Draft, role="writer", run_config=state["run_config"])

    draft, dropped = _without_unknown_citations(draft, notes.source_ids)
    if dropped:
        log.warning("writer: dropped citations to unknown sources: %s", sorted(set(dropped)))

    log.info(
        "writer: revision %d, %d words (~%ds of %ds), %d sources cited (%.1fs)",
        revision_count,
        draft.word_count,
        draft.estimated_seconds,
        plan.target_seconds,
        len(draft.cited_source_ids),
        time.perf_counter() - started,
    )
    return {
        "draft": draft,
        "revision_count": revision_count,
        "history": [
            RevisionRecord(
                kind="draft",
                revision=revision_count,
                note="human feedback" if rejected_by_human else "",
            )
        ],
    }
