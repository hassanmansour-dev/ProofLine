"""StubResearcher — fixed notes, no network. The Week 1 default (PROOFLINE_RESEARCHER=stub)."""

from __future__ import annotations

from proofline.schemas import Finding, Outline, ResearchNotes, RunConfig, Source

# Fixed, checked facts about loop exits (verified against the installed langgraph 1.2.16
# source), so a stubbed run still produces an honest, cited script.
_SOURCES = (
    Source(
        id="S1",
        url="https://pypi.org/project/langgraph/1.2.16/",
        title="langgraph 1.2.16 (package source)",
        content="GraphRecursionError; DEFAULT_RECURSION_LIMIT = 10007.",
    ),
    Source(
        id="S2",
        url="https://github.com/hassanmansour-dev/ProofLine/blob/main/docs/adr/0004-run-config-snapshot-pure-routing.md",
        title="Proofline ADR-0004: run config snapshot, pure routing",
        content="Revision guard (score threshold, max revisions) plus a recursion_limit backstop.",
    ),
)
_FINDINGS = (
    Finding(
        claim="LangGraph raises GraphRecursionError when a run takes more steps than its recursion_limit.",
        source_ids=["S1"],
        confidence="high",
    ),
    Finding(
        claim="In langgraph 1.2.16 the default recursion_limit is 10,007 steps.",
        source_ids=["S1"],
        confidence="high",
    ),
    Finding(
        claim="Proofline ends its critic loop when the score reaches a threshold or after a fixed number of revisions, and passes a recursion_limit on every run as a backstop.",
        source_ids=["S2"],
        confidence="high",
    ),
)


class StubResearcher:
    """Returns the same notes for every topic. Swap in WebResearcher for real research.

    It never researched the outline's questions, so it reports all of them as gaps: the
    writer then says what's unclear instead of filling in uncited claims.
    """

    async def research(self, topic: str, outline: Outline, run_config: RunConfig) -> ResearchNotes:
        return ResearchNotes(
            sources=list(_SOURCES), findings=list(_FINDINGS), gaps=list(outline.key_questions)
        )
