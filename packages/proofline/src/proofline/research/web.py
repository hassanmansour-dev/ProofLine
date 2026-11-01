"""WebResearcher — search + one LLM extraction call (PROOFLINE_RESEARCHER=web).

Runs the outline's queries concurrently through the SearchClient port (Tavily in
production), dedupes and ranks the results, assigns ids S1..Sn in code, and asks the
LLM for findings that cite those ids (ADR-0007, ADR-0011). Week 5 replaces this with
a web-search agent behind the same Researcher protocol.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from proofline.llm import StructuredLLM
from proofline.prompts.loader import load_prompt
from proofline.render import bullet_list, sources_block
from proofline.schemas import ExtractedFindings, Outline, ResearchNotes, RunConfig, Source
from proofline.search import SearchClient, SearchResult, SearchUnavailableError

log = logging.getLogger("proofline.research.web")


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
        log.warning("research: query failed: %r: %r", query, exc)
    if failures and len(failures) == len(queries):
        for _, exc in failures:
            if not isinstance(exc, SearchUnavailableError):
                raise exc
        raise SearchUnavailableError(f"all {len(queries)} search queries failed") from failures[0][
            1
        ]
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


class WebResearcher:
    def __init__(self, search: SearchClient, llm: StructuredLLM, *, concurrency: int = 4) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._search = search
        self._llm = llm
        self._concurrency = concurrency

    async def research(self, topic: str, outline: Outline, run_config: RunConfig) -> ResearchNotes:
        started = time.perf_counter()
        cfg = run_config
        results = await _search_all(
            self._search,
            outline.search_queries,
            max_results=cfg.results_per_query,
            concurrency=self._concurrency,
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
            log.warning("research: no usable sources for %d queries", len(outline.search_queries))
            return ResearchNotes(sources=[], findings=[], gaps=list(outline.key_questions))

        prompt = load_prompt("researcher").render(
            topic=topic,
            angle=outline.angle,
            key_questions=bullet_list(outline.key_questions),
            sources=sources_block(sources),
        )
        extracted = await self._llm.generate(
            prompt, ExtractedFindings, role="researcher", run_config=cfg
        )
        log.info(
            "research: %d results -> %d sources, %d findings (%.1fs)",
            len(results),
            len(sources),
            len(extracted.findings),
            time.perf_counter() - started,
        )
        return ResearchNotes(sources=sources, findings=extracted.findings, gaps=extracted.gaps)
