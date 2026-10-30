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

import asyncio
import logging
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import load_prompt
from proofline.render import bullet_list, sources_block
from proofline.schemas import ExtractedFindings, Finding, ResearchNotes, Source
from proofline.search import SearchClient, SearchResult
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.researcher")


def normalize_url(url: str) -> str:
    """Canonical form for dedupe. Implemented — use it, don't re-derive it."""
    parts = urlsplit(url.strip())
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


async def _search_all(
    search: SearchClient, queries: list[str], *, max_results: int, concurrency: int
) -> list[SearchResult]:
    """Run every query concurrently (bounded). One failed query doesn't sink the others."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(query: str) -> list[SearchResult] | BaseException:
        async with semaphore:
            try:
                return await search.search(query, max_results=max_results)
            except Exception as exc:
                return exc

    outcomes = await asyncio.gather(*(one(q) for q in queries))
    failures = [
        (q, o) for q, o in zip(queries, outcomes, strict=True) if isinstance(o, BaseException)
    ]
    for query, exc in failures:
        log.warning("researcher: query failed: %r: %r", query, exc)
    if failures and len(failures) == len(queries):
        raise RuntimeError(f"all {len(queries)} search queries failed") from failures[0][1]
    return [r for o in outcomes if not isinstance(o, BaseException) for r in o]


def _rank(results: list[SearchResult]) -> list[SearchResult]:
    """Dedupe by normalized URL (keep the highest score), then sort by score desc, None last.

    Ties keep first-seen order, so the result is deterministic for a given search output.
    """
    best: dict[str, SearchResult] = {}
    for r in results:
        key = normalize_url(r.url)
        kept = best.get(key)
        if kept is None or (r.score is not None and (kept.score is None or r.score > kept.score)):
            best[key] = r
    return sorted(best.values(), key=lambda r: (r.score is None, -(r.score or 0.0)))


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
    deps = runtime.context
    cfg = state["run_config"]
    plan = state["plan"]

    results = await _search_all(
        deps.search,
        plan.search_queries,
        max_results=cfg.results_per_query,
        concurrency=deps.settings.search_concurrency,
    )
    ranked = _rank(results)[: cfg.max_sources]
    # Ids are assigned here, in code, never by the model (ADR-0007).
    sources = [
        Source(
            id=f"S{i}",
            url=r.url,
            title=r.title,
            content=r.content[: cfg.max_source_chars],
            score=r.score,
        )
        for i, r in enumerate(ranked, 1)
    ]

    if not sources:
        log.warning("researcher: no usable sources for %d queries", len(plan.search_queries))
        return {
            "research_notes": ResearchNotes(sources=[], findings=[], gaps=list(plan.key_questions))
        }

    prompt = load_prompt("researcher").render(
        topic=state["topic"],
        angle=plan.angle,
        key_questions=bullet_list(plan.key_questions),
        sources=sources_block(sources),
    )
    extracted = await deps.llm.generate(
        prompt, ExtractedFindings, role="researcher", run_config=cfg
    )
    findings = _known_findings(extracted.findings, {s.id for s in sources})

    log.info(
        "researcher: %d results -> %d sources, %d findings, %d gaps (%.1fs)",
        len(results),
        len(sources),
        len(findings),
        len(extracted.gaps),
        time.perf_counter() - started,
    )
    return {
        "research_notes": ResearchNotes(sources=sources, findings=findings, gaps=extracted.gaps)
    }
