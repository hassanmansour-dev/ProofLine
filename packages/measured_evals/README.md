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
quotes the matched text. Judge-model metrics and golden sets come in GROW-14, the
injection suite in GROW-15 (see `docs/PLAN.md`).

`measured_evals.cost` counts token usage (`Usage`, with prompt-cache reads and writes
apart from normal input) and prices it from a table you pass in; a model the table
doesn't list costs `None`, never 0.
