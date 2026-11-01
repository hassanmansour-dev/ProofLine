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


# A run of adjacent citation-like brackets ("[S1][S9]", "[s3]", "[S1; S9]", "[S1-S3]")
# plus the spaces before it, so the whole run can be rebuilt with correct spacing.
_CITATION_RUN_RE = re.compile(r"(?P<space>[ \t]*)(?P<run>(?:\[\s*[Ss]\s*\d+[^\[\]]*\])+)")
_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
_LOOSE_ID_RE = re.compile(r"[Ss]\s*(\d+)")


def _parse_bracket(inner: str) -> list[str] | None:
    """Ids in one bracket, normalized ("s 3; S5" -> ["S3", "S5"]); None if unparseable (a range)."""
    if re.sub(r"[,;\s]", "", _LOOSE_ID_RE.sub("", inner)):
        return None
    return [f"S{n}" for n in _LOOSE_ID_RE.findall(inner)]


def _clean_citations(text: str, known: set[str], dropped: list[str]) -> str:
    """Rewrite citations to canonical [S1][S2], keeping only known ids (ADR-0007, ADR-0011)."""

    def rebuild(m: re.Match[str]) -> str:
        brackets = _BRACKET_RE.findall(m.group("run"))
        if all(CITATION_RE.fullmatch(f"[{b}]") for b in brackets):
            ids = [i.strip() for b in brackets for i in b.split(",")]
            if all(i in known for i in ids):
                return m.group(0)  # canonical and known: leave untouched
        kept: list[str] = []
        for inner in brackets:
            parsed = _parse_bracket(inner)
            if parsed is None:
                dropped.append(f"[{inner}]")
                continue
            kept.extend(i for i in parsed if i in known)
            dropped.extend(i for i in parsed if i not in known)
        if not kept:
            return ""
        return m.group("space") + "".join(f"[{i}]" for i in dict.fromkeys(kept))

    cleaned = _CITATION_RUN_RE.sub(rebuild, text)
    return cleaned.strip() if cleaned != text and text == text.strip() else cleaned


def _without_unknown_citations(draft: Draft, known: set[str]) -> tuple[Draft, list[str]]:
    """Normalize inline citations and remove ids the researcher never assigned."""
    dropped: list[str] = []

    def clean(text: str) -> str:
        return _clean_citations(text, known, dropped)

    sections = [
        s.model_copy(update={"narration": clean(s.narration), "on_screen": clean(s.on_screen)})
        for s in draft.sections
    ]
    update = {"hook": clean(draft.hook), "call_to_action": clean(draft.call_to_action)}
    cleaned = draft.model_copy(update={**update, "sections": sections})
    return (cleaned if cleaned != draft else draft), dropped


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
        log.warning("writer: dropped unknown or malformed citations: %s", sorted(set(dropped)))

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
