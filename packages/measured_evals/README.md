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
scores (an empty result never passes). Today `evaluate()` is a stub that returns no
scores, so nothing gated on it can pass; the first real checks land in GROW-14
(see `docs/PLAN.md`).
