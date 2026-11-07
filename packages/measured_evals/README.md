# measured-evals

Evaluation and security checks for LLM pipelines: quality metrics against a golden
set, regression gates for CI, per-run cost budgets and prompt-injection tests. It
is developed alongside [Proofline](../../README.md), which uses it to gate what
it ships, but it has **no dependency on Proofline**: it never imports the pipeline
app, so any project (including the planned Measured Lab web app) can install it on
its own. A test in `tests/test_boundary.py` enforces that boundary. Empty skeleton
for now; the `evaluate()` interface lands in step C1 and the first checks in
GROW-14 (see `docs/PLAN.md`).
