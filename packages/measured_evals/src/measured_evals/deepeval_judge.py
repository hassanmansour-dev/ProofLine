"""DeepEval adapter for the model-graded tier (GROW-14): one GEval metric per judge case.

DeepEval is **not** a dependency of this package. It is installed only where the
model-graded tier runs, from `requirements-judge.txt` (every version pinned), and
imported here by name at call time. So the package stays dependency-free and the
deterministic tier never loads it.

Checked against the installed `deepeval==4.3.4`, not only its docs:
- the parameter enum is `deepeval.test_case.SingleTurnParams` (older docs and
  examples call it `LLMTestCaseParams`, which 4.3.4 no longer exports);
- with explicit `evaluation_steps`, GEval makes **one** model call per `measure()`
  (with only `criteria` it makes a second call to write the steps first);
- GEval asks the model for an integer 0-10 and returns it divided by 10;
- `AnthropicModel` reads `ANTHROPIC_API_KEY` and sends no temperature unless one is
  set (the same rule as ADR-0015).

`tests/test_deepeval_judge.py` runs this adapter against the real library with a
fake judge model: no key, no network, no cost.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from measured_evals.judge import (
    JUDGE_THRESHOLD,
    JudgeAnswer,
    JudgeCase,
    JudgeError,
    JudgeFn,
    case_input,
    evaluation_steps,
)


def _import(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise JudgeError(
            f"{name} is not installed: the model-graded tier needs "
            "packages/measured_evals/requirements-judge.txt (docs/MODEL_GRADED.md)"
        ) from exc


def build_deepeval_judge(
    model_name: str | None = None, *, model: Any = None, threshold: float = JUDGE_THRESHOLD
) -> JudgeFn:
    """A `JudgeFn` backed by GEval. Pass `model` (a `DeepEvalBaseLLM`) to inject a fake."""
    # Off unless the caller turned it on: an eval run reports nothing to a third party.
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    metrics = _import("deepeval.metrics")
    test_case = _import("deepeval.test_case")
    params = test_case.SingleTurnParams
    if model is None:
        if not model_name:
            raise JudgeError("a judge model name is required")
        _import("anthropic")
        model = _import("deepeval.models").AnthropicModel(model=model_name)

    def judge(case: JudgeCase) -> JudgeAnswer:
        evaluation_params = [params.INPUT, params.ACTUAL_OUTPUT]
        if case.sources:
            evaluation_params.append(params.CONTEXT)
        metric = metrics.GEval(
            name=case.case_id,
            evaluation_steps=evaluation_steps(case),
            evaluation_params=evaluation_params,
            model=model,
            threshold=threshold,
            async_mode=False,
        )
        metric.measure(
            test_case.LLMTestCase(
                input=case_input(case),
                actual_output=case.output,
                context=list(case.sources) or None,
            ),
            _show_indicator=False,
        )
        if metric.score is None:
            raise JudgeError(f"{case.case_id}: GEval returned no score")
        return JudgeAnswer(score=float(metric.score), reason=str(metric.reason or ""))

    return judge
