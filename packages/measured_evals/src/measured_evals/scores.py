"""The eval interface: `evaluate(output, context) -> EvalScores` (C1, ADR-0014; C7, ADR-0018).

Generic by design: an output is plain text and the context is a plain mapping, so
any app can call it without this package knowing the app's types. Proofline adapts
its own objects to these arguments, never the other way round (ADR-0012).

`evaluate` runs the deterministic guard-v0 checks (`measured_evals.checks`): quality,
injection, PII. Judge-model metrics arrive in GROW-14. It fails closed: an
`EvalScores` with no scores never passes, and a check that raises is not caught.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from measured_evals.checks import DEFAULT_CHECKS, CheckFn

STUB_EVALUATOR = "stub"
GUARD_EVALUATOR = "guard-v0"
MAX_REASON = 200
DEFAULT_THRESHOLD = 0.7


@dataclass(frozen=True, slots=True)
class EvalScores:
    """Per-metric scores in [0, 1] from one evaluation of one output.

    `passed` is computed here from the scores, never reported by a metric or a
    judge model, the same rule as the critic's overall score (ADR-0008).

    `reasons` explains failing metrics in a few words. It never holds the text that
    triggered a check (a matched email, say): it's shown to humans and logged.
    """

    scores: Mapping[str, float] = field(default_factory=dict)
    threshold: float = DEFAULT_THRESHOLD
    evaluator: str = STUB_EVALUATOR
    reasons: Mapping[str, str] = field(default_factory=dict)

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
        for name, reason in self.reasons.items():
            if name not in self.scores:
                raise ValueError(f"reason for {name!r}, which has no score")
            if not isinstance(reason, str) or not 0 < len(reason) <= MAX_REASON:
                raise ValueError(f"reason for {name!r} must be 1-{MAX_REASON} characters")
        # copy, so a caller mutating its own dict can't change this result
        object.__setattr__(self, "scores", dict(self.scores))
        object.__setattr__(self, "reasons", dict(self.reasons))

    @property
    def passed(self) -> bool:
        """True only if there is at least one score and every score meets the threshold."""
        return bool(self.scores) and min(self.scores.values()) >= self.threshold

    @property
    def failing(self) -> tuple[str, ...]:
        """Names of the metrics below the threshold, sorted."""
        return tuple(sorted(n for n, v in self.scores.items() if v < self.threshold))


async def evaluate(
    output: str,
    context: Mapping[str, object] | None = None,
    *,
    checks: Iterable[tuple[str, CheckFn]] = DEFAULT_CHECKS,
) -> EvalScores:
    """Score `output` given `context` (see `measured_evals.checks` for the keys).

    Runs every check in order; one score and, on failure, one reason per check. A
    check that raises propagates: a broken guard fails loudly, never as a silent 0.
    Async because judge-model metrics (GROW-14) will call models.
    """
    ctx: Mapping[str, object] = context or {}
    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for name, fn in checks:
        result = fn(output, ctx)
        scores[name] = result.score
        if result.reason is not None:
            reasons[name] = result.reason
    return EvalScores(scores, evaluator=GUARD_EVALUATOR, reasons=reasons)
