# measured-evals

Evaluation and security checks for LLM pipelines: quality metrics against a golden
set, regression gates for CI, per-run cost budgets and prompt-injection tests. It
is developed alongside [Proofline](../../README.md), which uses it to gate what
it ships, but it has **no dependency on Proofline**: it never imports the pipeline
app or Measured Ops, so any project (including the planned Measured Lab web app) can
install it on its own. A test in `tests/test_boundary.py` enforces that boundary,
and the package has no runtime dependencies.

The interface is `await evaluate(output, context) -> EvalScores`: plain text and a
plain mapping in, per-metric scores in [0, 1] out, with `passed` computed from the
scores (an empty result never passes). It runs **guard-v0**: deterministic quality,
prompt-injection and PII checks, each scoring 0 or 1 with a short reason that never
quotes the matched text. Judge-model metrics come in GROW-14, the injection suite in
GROW-15 (see `docs/PLAN.md`).

`measured_evals.golden` defines the golden-set format: topics with fixture sources and
checkable `expected` / `must_not` assertions in TOML, validated on load.
`load_golden_set()` returns the bundled **draft** set of 20 topics; every item is
`needs_eval` until its owner approves it, and `GoldenSet.approved` returns only
approved items (today: none). See [`docs/GOLDEN_SET.md`](../../docs/GOLDEN_SET.md).

`measured_evals.inbound` screens content read from outside (a web page, a tool
result) for instruction-like text, with a small demo corpus that keeps its known
misses in it; a flag never grants trust to unflagged text. `measured_evals.budget`
stops a run before a model call would cross a cap on calls, tokens or cost, and
fails closed when a cost is unknown. Neither is wired into the pipeline yet
([ADR-0021](../../docs/adr/0021-inbound-injection-suite-and-budget-guard.md)).

`measured_evals.cost` counts token usage (`Usage`, with prompt-cache reads and writes
apart from normal input) and prices it from a table you pass in; a model the table
doesn't list costs `None`, never 0.

`measured_evals.judge` is the model-graded tier: a judge model answers the golden
set's `judge` assertions through DeepEval's GEval, with a promptfoo config as a
second grader. It runs only with an API key, only on saved outputs that passed the
deterministic tier, under a call budget, and its scores are `needs_eval`. DeepEval is
not a dependency: it installs from `requirements-judge.txt` where this tier runs.
**It has never been run against a model; no judge score exists.**
See [`docs/MODEL_GRADED.md`](../../docs/MODEL_GRADED.md).

`measured_evals.suites` is the deterministic eval tier: structure, word limit,
citations that resolve, no uncited body sentences, then each golden item's own
assertions. `python -m measured_evals run` runs it with a fake writer, no model and
no key; `measured_evals.baseline` is the regression-baseline format, in which a
placeholder is reported and never enforced. See [`docs/EVALS.md`](../../docs/EVALS.md).

`measured_evals.calibration` checks a model critic against a person: it builds a
blind scoring sheet (CSV + Markdown) from saved outputs with a blank `human_score`
column, and once the sheet is filled it reports Cohen's kappa on pass/fail at the
critic's threshold, with the counts behind it. It refuses to compute on a blank
cell. No calibration has been run yet. See [`docs/CALIBRATION.md`](../../docs/CALIBRATION.md).
