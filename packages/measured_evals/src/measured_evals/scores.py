"""The eval interface: `evaluate(output, context) -> EvalScores` (step C1, ADR-0014).

Generic by design: an output is plain text and the context is a plain mapping, so
any app can call it without this package knowing the app's types. Proofline adapts
its own objects to these arguments, never the other way round (ADR-0012).

`evaluate` is a stub until GROW-14 adds real metrics. It fails closed: the stub
returns no scores, and an `EvalScores` with no scores never passes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

STUB_EVALUATOR = "stub"
DEFAULT_THRESHOLD = 0.7


@dataclass(frozen=True, slots=True)
class EvalScores:
    """Per-metric scores in [0, 1] from one evaluation of one output.

    `passed` is computed here from the scores, never reported by a metric or a
    judge model, the same rule as the critic's overall score (ADR-0008).
    """

    scores: Mapping[str, float] = field(default_factory=dict)
    threshold: float = DEFAULT_THRESHOLD
    evaluator: str = STUB_EVALUATOR

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {self.threshold}")
        for name, value in self.scores.items():
            if not name:
                raise ValueError("metric names must be non-empty")
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"score for {name!r} must be a number, got {value!r}")
            if math.isnan(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"score for {name!r} must be in [0, 1], got {value}")
        # copy, so a caller mutating its own dict can't change this result
        object.__setattr__(self, "scores", dict(self.scores))

    @property
    def passed(self) -> bool:
        """True only if there is at least one score and every score meets the threshold."""
        return bool(self.scores) and min(self.scores.values()) >= self.threshold

    @property
    def failing(self) -> tuple[str, ...]:
        """Names of the metrics below the threshold, sorted."""
        return tuple(sorted(n for n, v in self.scores.items() if v < self.threshold))


async def evaluate(output: str, context: Mapping[str, object] | None = None) -> EvalScores:
    """Score `output` given `context` (task, sources, rubric name, ...).

    Stub: returns an empty result from the "stub" evaluator, which never passes,
    so nothing gated on it can be auto-approved before real metrics exist (GROW-14).
    Async because real metrics call judge models.
    """
    del output, context  # unused until GROW-14
    return EvalScores()
