"""researcher — outline → ResearchNotes, through the Researcher port (ADR-0012).

Contract (docs/ARCHITECTURE.md §4):
    reads:   topic, outline, run_config
    writes:  research_notes
    deps:    runtime.context.researcher (StubResearcher by default; WebResearcher with
             PROOFLINE_RESEARCHER=web; a web-search agent in Week 5)
The node owns the one guarantee every implementation must meet: findings cite only
source ids that exist (ADR-0007). graph.py wires it with RETRY_POLICY below.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy

from proofline.context import Deps
from proofline.research.web import normalize_url
from proofline.schemas import Finding
from proofline.search import SearchUnavailableError
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.researcher")

# For graph.py: retry only transient search outages (ADR-0011).
RETRY_POLICY = RetryPolicy(max_attempts=3, retry_on=SearchUnavailableError)

__all__ = ["RETRY_POLICY", "normalize_url", "researcher"]


def _known_findings(findings: list[Finding], known_ids: set[str]) -> list[Finding]:
    """Drop citations to ids we never assigned; drop findings left with no citation (ADR-0007)."""
    kept: list[Finding] = []
    dropped_ids = 0
    for f in findings:
        ids = [i for i in f.source_ids if i in known_ids]
        dropped_ids += len(f.source_ids) - len(ids)
        if ids:
            kept.append(
                f if len(ids) == len(f.source_ids) else f.model_copy(update={"source_ids": ids})
            )
    if dropped_ids or len(kept) < len(findings):
        log.warning(
            "researcher: dropped %d unknown source ids and %d uncited findings",
            dropped_ids,
            len(findings) - len(kept),
        )
    return kept


async def researcher(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    started = time.perf_counter()
    notes = await runtime.context.researcher.research(
        state["topic"], state["outline"], state["run_config"]
    )
    findings = _known_findings(notes.findings, notes.source_ids)
    if findings != notes.findings:
        notes = notes.model_copy(update={"findings": findings})
    log.info(
        "researcher: %d sources, %d findings, %d gaps (%.1fs)",
        len(notes.sources),
        len(notes.findings),
        len(notes.gaps),
        time.perf_counter() - started,
    )
    return {"research_notes": notes}
