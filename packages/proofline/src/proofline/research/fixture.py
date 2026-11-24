"""FixtureResearcher: a golden item's own sources, no search (`proofline golden`).

The golden set (`measured_evals`, GROW-13) gives each item fixture sources with ids
`S1..Sn`, and its assertions check citations against those ids. So this researcher keeps
the ids **as written** (they come from a reviewed file, not from a model) and runs the
same findings step as `WebResearcher` (`extract_findings`): the pipeline is measured as it
really runs, minus the search. Source text reaches the model inside escaped `<source>`
tags as untrusted data; the bait items test exactly that.

An item with no sources gets empty notes, its outline's key questions as gaps, and no
model call, like a search that found nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from measured_evals.golden import GoldenSource
from proofline.llm import StructuredLLM
from proofline.research.web import extract_findings
from proofline.schemas import Outline, ResearchNotes, RunConfig, Source


class FixtureResearcher:
    def __init__(self, item_id: str, sources: Sequence[GoldenSource], llm: StructuredLLM) -> None:
        self.item_id = item_id
        self.sources = tuple(sources)
        self._llm = llm

    async def research(self, topic: str, outline: Outline, run_config: RunConfig) -> ResearchNotes:
        sources = [
            Source(
                id=s.id,
                url=f"golden:{self.item_id}#{s.id}",  # a fixture, not a web page
                title=s.title,
                content=s.text[: run_config.max_source_chars],
            )
            for s in self.sources
        ]
        if not sources:
            return ResearchNotes(sources=[], findings=[], gaps=list(outline.key_questions))
        extracted = await extract_findings(self._llm, topic, outline, sources, run_config)
        return ResearchNotes(sources=sources, findings=extracted.findings, gaps=extracted.gaps)
