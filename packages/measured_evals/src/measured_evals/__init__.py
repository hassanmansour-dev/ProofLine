"""measured_evals — evaluation and security checks for LLM pipelines.

Standalone: this package never imports the proofline app or measured_ops
(enforced by tests/test_boundary.py), so other projects can depend on it unchanged.
"""

from importlib.metadata import version

from measured_evals.checks import Check, CheckResult
from measured_evals.cost import (
    AgentTotal,
    LongContext,
    ModelPrice,
    Usage,
    cost,
    load_prices,
    total_by_agent,
)
from measured_evals.golden import (
    Assertion,
    GoldenItem,
    GoldenSet,
    GoldenSetError,
    GoldenSource,
    load_golden_set,
    parse_golden_set,
)
from measured_evals.scores import EvalScores, evaluate

__version__ = version("measured-evals")

__all__ = [
    "AgentTotal",
    "Assertion",
    "Check",
    "CheckResult",
    "EvalScores",
    "GoldenItem",
    "GoldenSet",
    "GoldenSetError",
    "GoldenSource",
    "LongContext",
    "ModelPrice",
    "Usage",
    "__version__",
    "cost",
    "evaluate",
    "load_golden_set",
    "load_prices",
    "parse_golden_set",
    "total_by_agent",
]
