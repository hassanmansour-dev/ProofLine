"""researcher — Plan → ResearchNotes. Week 1, S3. Gets RetryPolicy(max_attempts=3) in graph.py.

Contract (docs/ARCHITECTURE.md §4, ADR-0007):
    reads:   topic, plan, run_config
    writes:  research_notes
    deps:    runtime.context.search, runtime.context.llm, settings.search_concurrency
    LLM:     prompts/researcher.md, role="researcher", schema=ExtractedFindings
Steps:
    1. Run every plan.search_queries concurrently via deps.search.search(q, max_results=cfg.results_per_query),
       bounded by asyncio.Semaphore(settings.search_concurrency).
       One failed query must not sink the others — collect failures, log WARNING;
       if ALL queries fail, raise (RetryPolicy retries the node).
    2. Dedupe by normalized URL (lowercase scheme+host, strip trailing "/", drop fragment
       and utm_* params) — see normalize_url below. Keep the highest-score duplicate.
    3. Sort by score desc (None last), keep cfg.max_sources, truncate content to
       cfg.max_source_chars.
    4. Assign ids S1..Sn IN CODE → list[Source].
    5. Render prompt with sources_block(sources) (render.py) — content stays inside <source> tags.
    6. extracted = await llm.generate(..., ExtractedFindings, role="researcher", ...)
    7. Drop finding.source_ids not in the known ids; drop findings left with no ids.
       Log WARNING with counts.
    8. return {"research_notes": ResearchNotes(sources=..., findings=..., gaps=extracted.gaps)}
Edge: zero sources after search → skip the LLM, return notes with no findings and
      gaps = plan.key_questions (writer must state limits; critic penalizes).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.state import ScriptState


def normalize_url(url: str) -> str:
    """Canonical form for dedupe. Implemented — use it, don't re-derive it."""
    parts = urlsplit(url.strip())
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


async def researcher(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    raise NotImplementedError("Week 1 S3 — see module docstring")
