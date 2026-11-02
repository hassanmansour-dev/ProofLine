# ADR-0012: uv workspace with a standalone eval package; Week 1 reset to one short format

- Status: accepted (2026-11-01, from Hassan's project brief)
- Date: 2026-11-01
- Phase/Week: P1W1

## Context
Hassan's project brief (2026-11-01) sets three things the kickoff kit didn't have:

1. **The eval and security layer is its own package from day one.** Proofline uses
   it, a later web app (Measured Lab) will sit on top of it, and it will be
   published as open source. It must never import the Proofline app.
2. **One output format.** A short technical script for LinkedIn posts and
   1-minute videos, under 200 words, instead of 60 s / 10 min video scripts.
3. **Week 1 is a straight line:** planner → researcher (a stub behind a
   `Researcher` protocol) → writer → finalizer. No critic, no loop, no interrupt.

The brand name ("Build Clearly") was hard-coded in every prompt; Hassan may change it.

## Decision
- **Layout.** A uv workspace: `packages/proofline` (the pipeline app) and
  `packages/measured_evals` (PyPI name `measured-evals`). The root `pyproject.toml`
  is a non-package workspace root holding the dev toolchain and shared
  ruff / mypy / pytest config. `measured_evals` has no dependency on `proofline`,
  and `packages/measured_evals/tests/test_boundary.py` fails if it ever declares
  one or imports it. Dependencies point one way only: app → evals.
- **State** (`state.py`): topic, audience, research_notes, draft, critique,
  score, revision_count, approved, feedback, as in the brief, plus `outline` (planner
  output), `final_script` (finalizer output) and `run_config` (ADR-0004).
  `initial_state()` sets every default; `approved=None` means not reviewed yet.
  The critic writes `score` (= `Critique.overall`) and the router reads it.
- **Format.** `VideoFormat` and `TARGET_SECONDS` are gone. `RunConfig.max_words`
  (default 200, `PROOFLINE_MAX_WORDS`) is the only length. `Outline` replaces
  `Plan` (no `target_seconds`; 2–4 beats). `Draft` is title / hook / body /
  call_to_action. Over the limit, the writer asks for one cut and keeps the
  result either way; the Week 2 critic's `length_fit` scores it. Rubric v2 measures
  `length_fit` against the word limit.
- **Brand.** `RunConfig.brand_name` (`PROOFLINE_BRAND_NAME`, default "Build
  Clearly") fills a `{brand}` placeholder in the planner, writer and critic
  prompts. It's in the run snapshot, so a resumed run keeps its brand.
- **Not removed:** the critic node (wired in Week 2), human_review (Week 3), the
  checkpointer factory and the API stub (Week 3), and the Tavily researcher, which
  stays as a second implementation behind the `Researcher` protocol.

## Alternatives considered
- **Evals as a module inside `proofline`** — an import from the app would leak in
  sooner or later, and publishing it later means a painful extraction.
- **A separate repository for `measured_evals`** — the boundary is just as clean, but
  every change spanning both needs two PRs and a release. One workspace keeps
  it to one PR until there's a real outside user.
- **Keep both video formats** — the brief scopes one format; a second can come back
  as a `RunConfig` field without touching the graph.

## Consequences
- Good: the eval package can be published or reused without the app. One length
  and one brand setting replace scattered constants. State matches the brief's
  vocabulary.
- Bad / cost: `Outline` / `Draft` are breaking changes for any checkpoint holding
  the old shapes (none exist; still no live runs). Paths in docs and tooling moved.
- Revisit when: `measured_evals` gets its first outside user (consider its own repo
  and release cadence), or a second output format is needed.
