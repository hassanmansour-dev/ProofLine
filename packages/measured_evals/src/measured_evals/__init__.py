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
from measured_evals.scores import EvalScores, evaluate

__version__ = version("measured-evals")

__all__ = [
    "AgentTotal",
    "Check",
    "CheckResult",
    "EvalScores",
    "LongContext",
    "ModelPrice",
    "Usage",
    "__version__",
    "cost",
    "evaluate",
    "load_prices",
    "total_by_agent",
]
