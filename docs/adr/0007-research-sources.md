# ADR-0007: Research via Tavily behind a port; code-assigned source ids; content is untrusted

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W1

## Context
Scripts must be accurate and checkable. LLMs invent citations; web pages can
contain prompt injections.

## Decision
- Search provider: Tavily (returns cleaned page content, built for LLM use),
  behind `SearchClient` so it can be swapped.
- Queries come from the planner; run concurrently (semaphore 4); dedupe by
  normalized URL; keep top `max_sources` (default 8); truncate each to
  `max_source_chars` (default 4000).
- **Source ids `S1..Sn` are assigned in Python**, never by the LLM. Findings and
  drafts cite ids; ids not in the source list are dropped and logged.
- Source text is passed inside `<source id=... url=...>` tags with an explicit
  instruction that it is reference data and any instructions inside it are ignored.

## Alternatives considered
- **LLM-generated citations (URLs in prose)** — unverifiable, often hallucinated.
- **Full-page fetch + own extraction** — more control, more code; not the Phase 1 lesson.

## Consequences
- Good: every claim traces to a real URL; the critic can check claims against notes.
- Bad: quality bounded by Tavily's snippets. Phase 2 adds docs/GitHub and
  developer-discussion specialists.
