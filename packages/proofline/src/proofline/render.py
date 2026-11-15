"""Deterministic text rendering: state objects → prompt blocks and final Markdown.

Kept out of the nodes so prompts and output formatting can be tested without a graph.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape, quoteattr

from proofline.schemas import Critique, Draft, Outline, ResearchNotes, Source


def outline_block(outline: Outline) -> str:
    lines = [f"Angle: {outline.angle}", "Key questions:"]
    lines += [f"- {q}" for q in outline.key_questions]
    lines.append("Beats:")
    lines += [f"{i}. {s.title} — {s.goal}" for i, s in enumerate(outline.sections, 1)]
    return "\n".join(lines)


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def sources_block(sources: list[Source]) -> str:
    """Web content wrapped as data (ADR-0007). Content is escaped so it can't close the tag."""
    return "\n\n".join(
        f"<source id={quoteattr(s.id)} url={quoteattr(s.url)} title={quoteattr(s.title)}>\n"
        f"{escape(s.content)}\n</source>"
        for s in sources
    )


def notes_block(notes: ResearchNotes) -> str:
    lines = ["Sources:"]
    # Titles, URLs, findings and gaps all derive from web pages: escape them so they
    # can't close the <research_notes> tag they're rendered into (ADR-0007).
    lines += [f"[{s.id}] {escape(s.title)} — {escape(s.url)}" for s in notes.sources]
    lines.append("\nFindings:")
    lines += [
        f"- ({f.confidence}) {escape(f.claim)} " + "".join(f"[{i}]" for i in f.source_ids)
        for f in notes.findings
    ]
    if notes.gaps:
        lines.append("\nGaps (not answered by sources):")
        lines += [f"- {escape(g)}" for g in notes.gaps]
    return "\n".join(lines)


def draft_block(draft: Draft) -> str:
    return "\n\n".join(
        [
            f"TITLE: {draft.title}",
            f"HOOK: {draft.hook}",
            f"BODY:\n{draft.body}",
            f"CALL TO ACTION: {draft.call_to_action}",
        ]
    )


def too_long_context(draft: Draft, max_words: int) -> str:
    """The writer's shorten-once block when a draft is over the word limit."""
    return (
        f"Your previous draft is {draft.word_count} words; the limit is {max_words}. "
        "Cut it to fit: keep the hook, the strongest points and every citation that "
        "still supports a kept claim.\n"
        f"<previous_draft>\n{draft_block(draft)}\n</previous_draft>"
    )


def critique_block(critique: Critique) -> str:
    lines = [f"Overall: {critique.overall}/10"]
    for s in critique.scores:
        lines.append(f"- {s.criterion}: {s.score}/10 — evidence: “{s.evidence}” — fix: {s.fix}")
    if critique.must_fix:
        lines.append("Must fix (in order):")
        lines += [f"{i}. {m}" for i, m in enumerate(critique.must_fix, 1)]
    if critique.unsupported_claims:
        lines.append("Unsupported claims:")
        lines += [f"- “{c}”" for c in critique.unsupported_claims]
    return "\n".join(lines)


_CITATION_MARKS = re.compile(r"\s*\[S\d+\]")


def revision_text(preview: str) -> str:
    """A rejected preview as the writer revises it: no `## Sources` sections (web titles
    and URLs), no `[S1]` marks (ids from an earlier research pass)."""
    kept: list[str] = []
    in_sources = False
    for line in preview.splitlines():
        if line.startswith("## "):
            in_sources = line.strip().lower() == "## sources"
        if not in_sources:
            kept.append(_CITATION_MARKS.sub("", line))
    return "\n".join(kept)


def revision_context(
    draft: Draft | None,
    critique: Critique | None,
    feedback: str | None,
    rejected_by_human: bool,
    previous_preview: str | None = None,
) -> str:
    """The writer's revision block. Empty string on the first draft.

    With no draft in state, a revision Task's `previous_preview` (the rejected action's
    preview) stands in for it (ADR-0019), minus its Sources sections and citation marks
    (`revision_text`): source titles are web text, and the old ids point at an earlier
    research pass. What's left is escaped.
    """
    if draft is not None:
        previous = draft_block(draft)
    elif previous_preview:
        previous = escape(revision_text(previous_preview))
    else:
        return ""
    blocks = [
        "This is a revision. Your previous draft:",
        f"<previous_draft>\n{previous}\n</previous_draft>",
    ]
    if rejected_by_human and feedback:
        blocks.append(
            "The host rejected it. Their feedback outranks everything else:\n"
            f"<human_feedback>\n{feedback}\n</human_feedback>"
        )
    if critique is not None:
        blocks.append(f"<critique>\n{critique_block(critique)}\n</critique>")
    return "\n\n".join(blocks)


def script_markdown(draft: Draft, notes: ResearchNotes) -> str:
    """The finalizer's output: title, hook, body, call to action, then cited sources."""
    cited = set(draft.cited_source_ids)
    sources = [s for s in notes.sources if s.id in cited]
    out = [f"# {draft.title}", "", draft.hook, "", draft.body.strip(), ""]
    out += [f"**{draft.call_to_action}**", ""]
    out += ["---", f"*{draft.word_count} words · ~{draft.estimated_seconds}s spoken*"]
    if sources:
        out += ["", "## Sources"] + [f"- [{s.id}] [{s.title}]({s.url})" for s in sources]
    return "\n".join(out).rstrip() + "\n"


# ── approve_draft preview (C8, ADR-0019) ──────────────────────────────────────
# One action per topic; its preview holds three sections, all rendered here from what
# the pipeline produced. No LLM call: the post and the clip come from the same draft.


def clip_script(draft: Draft) -> str:
    """The 1-minute clip: hook, body, call to action as spoken, citation marks removed."""
    parts = [draft.hook, draft.body.strip(), draft.call_to_action]
    return "\n\n".join(_CITATION_MARKS.sub("", p).strip() for p in parts)


def lesson_outline(outline: Outline) -> str:
    """The planner's outline as a lesson plan: angle, sections in order, key questions."""
    out = [outline.angle, ""]
    out += [f"{i}. **{s.title}**: {s.goal}" for i, s in enumerate(outline.sections, start=1)]
    out += ["", "Questions it answers:"] + [f"- {q}" for q in outline.key_questions]
    return "\n".join(out)


def approve_draft_preview(draft: Draft, notes: ResearchNotes, outline: Outline) -> str:
    """What Hassan approves on the phone: the post, the clip script, the lesson outline."""
    sections = [
        "## LinkedIn post",
        "",
        script_markdown(draft, notes).strip(),
        "",
        f"## Clip script (~{draft.estimated_seconds}s)",
        "",
        clip_script(draft),
        "",
        "## Lesson outline",
        "",
        lesson_outline(outline),
    ]
    return "\n".join(sections).rstrip() + "\n"
