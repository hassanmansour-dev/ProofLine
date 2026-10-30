# ADR-0009: Async end to end; uv, ruff, mypy strict, pytest

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W1

## Context
The researcher fans out I/O; FastAPI is async; LangGraph supports async nodes
and async checkpointers natively.

## Decision
- All nodes `async def`; graph invoked with `ainvoke` / `astream`.
- Tooling: `uv` (env + lock), `ruff` (lint + format), `mypy --strict` on `src/`,
  `pytest` + `anyio` for async tests. Python 3.12+.
- Markers: `live` (real APIs, opt-in). Week-target tests use `xfail(strict=True)`.

## Alternatives considered
- **Sync first, async later** — the API and checkpointer would need rework in Week 3.

## Consequences
- Good: no rework in Week 3; parallel search from day one.
- Bad: async stack traces are noisier.
