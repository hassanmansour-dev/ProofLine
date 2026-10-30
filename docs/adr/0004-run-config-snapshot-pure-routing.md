# ADR-0004: Snapshot run config into state; routing is pure

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W2

## Context
The critic loop depends on a threshold (8.0) and a revision budget (3). If those
come from live settings, a run resumed next week after a settings change
behaves differently from how it started, and routers need I/O to read config.

## Decision
- At run start, `Settings` is snapshotted into `state["run_config"]`
  (`RunConfig`: threshold, max_revisions, model ids, temperatures).
- `routing.py` contains only pure functions of state. No settings import,
  no logging side effects that matter, no LLM.
- `recursion_limit=40` on every invocation as a backstop.

## Alternatives considered
- **Read settings inside routers** — hidden I/O, non-reproducible resumes.
- **Hard-code constants** — fine until the first experiment.

## Consequences
- Good: routers are trivially unit-tested (table-driven); runs are reproducible
  and the config is visible in every checkpoint and trace.
- Bad: changing the threshold doesn't affect runs already in flight (intended).
