"""Deterministic text rendering: state objects → prompt blocks and final Markdown.

Kept out of the nodes so prompts and output formatting can be tested without a graph.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from proofline.schemas import Critique, Draft, Plan, ResearchNotes, Source


def plan_block(plan: Plan) -> str:
    lines = [f"Angle: {plan.angle}", f"Target: {plan.target_seconds}s", "Key questions:"]
    lines += [f"- {q}" for q in plan.key_questions]
    lines.append("Outline:")
    lines += [f"{i}. {s.title} — {s.goal}" for i, s in enumerate(plan.outline, 1)]
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
    parts = [f"TITLE: {draft.title}", f"HOOK: {draft.hook}"]
    for s in draft.sections:
        parts.append(f"## {s.heading}\n{s.narration}")
        if s.on_screen:
            parts.append(f"[on screen: {s.on_screen}]")
    parts.append(f"CALL TO ACTION: {draft.call_to_action}")
    return "\n\n".join(parts)


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


def revision_context(
    draft: Draft | None,
    critique: Critique | None,
    feedback: str | None,
    rejected_by_human: bool,
) -> str:
    """The writer's revision block. Empty string on the first draft."""
    if draft is None:
        return ""
    blocks = [
        "This is a revision. Your previous draft:",
        f"<previous_draft>\n{draft_block(draft)}\n</previous_draft>",
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
    cited = [s for s in notes.sources if s.id in set(draft.cited_source_ids)]
    out = [f"# {draft.title}", "", f"*~{draft.estimated_seconds}s · {draft.word_count} words*", ""]
    out += ["## Hook", draft.hook, ""]
    for s in draft.sections:
        out += [f"## {s.heading}", s.narration, ""]
        if s.on_screen:
            out += [f"> On screen: {s.on_screen}", ""]
    out += ["## Call to action", draft.call_to_action, ""]
    if cited:
        out += ["## Sources"] + [f"- [{s.id}] [{s.title}]({s.url})" for s in cited]
    return "\n".join(out).rstrip() + "\n"
