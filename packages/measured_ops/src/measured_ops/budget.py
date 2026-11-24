"""Per-run budget caps in the supervisor (C10, ADR-0023), on `measured_evals.budget`.

A budget is checked twice, both times **before** money is spent:
- in `call_agent`, before the agent runs, against what the run has spent so far
  (`OpsState.usage`: every earlier pass, revisions included);
- before **every model call** inside the agent, by `guard_budget`: a callback handler
  with `raise_error = True`, set through a context variable like `collect_usage`, that
  adds the calls made so far in this pass and raises `BudgetExceeded`, which aborts the
  call (verified on langchain-core 1.6.12: the exception reaches the caller unchanged).

The calls cap counts the call about to be made, so it's never crossed. Tokens and cost
are checked on spend so far: the supervisor doesn't know a call's `max_tokens`, so a run
can go over a token or cost cap by at most the one call that crossed it, and the next
call never happens. Unknown cost fails closed under a cost cap (ADR-0021).
The supervisor turns `BudgetExceeded` into a failed `AgentResult` with the pass's usage
recorded, which ends the run: raising would lose that usage, and a resumed run would get a
fresh allowance. Every supervisor reads its budget from `MEASURED_OPS_BUDGET_PATH` by default.

Caps come from TOML (`load_budget(path)`; `MEASURED_OPS_BUDGET_PATH`):

    version = 1
    [run]
    max_calls = 40
    max_tokens = 400000
    max_cost = 2.00     # the price table's currency

The demo table (`config/budget.demo.toml`) is a placeholder, not a recommendation; the
real caps are Hassan's and live in measured-ops-private.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from measured_evals import Usage
from measured_evals.budget import Budget, BudgetExceeded, check_budget
from measured_evals.cost import Prices
from measured_ops.usage import UsageCollector

DEMO_BUDGET = "budget.demo.toml"
ENV = "MEASURED_OPS_BUDGET_PATH"

__all__ = [
    "Budget",
    "BudgetExceeded",
    "BudgetGuard",
    "budget_from_env",
    "guard_budget",
    "load_budget",
]


def load_budget(path: Path | None = None) -> Budget:
    """The `[run]` caps of a budget table. No path = the demo table bundled here."""
    if path is None:
        raw = files("measured_ops").joinpath("config", DEMO_BUDGET).read_text("utf-8")
    else:
        raw = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    if data.get("version") != 1:
        raise ValueError("a budget table has version = 1")
    run = data.get("run")
    if not isinstance(run, dict) or set(run) - {"max_calls", "max_tokens", "max_cost"}:
        raise ValueError("a budget table has a [run] table of max_calls, max_tokens, max_cost")
    return Budget(**run)


def budget_from_env() -> Budget | None:
    """The budget `MEASURED_OPS_BUDGET_PATH` names, or None when it's unset (no caps)."""
    raw = os.environ.get(ENV, "").strip()
    return load_budget(Path(raw)) if raw else None


class BudgetGuard(BaseCallbackHandler):
    """Raises BudgetExceeded before a model call once the run's spend crosses a cap."""

    raise_error = True  # abort the call, don't just log

    def __init__(
        self, budget: Budget, prices: Prices, prior: Sequence[Usage], collector: UsageCollector
    ) -> None:
        self.budget = budget
        self.prices = prices
        self.prior = list(prior)
        self.collector = collector

    def _check(self) -> None:
        spent = [*self.prior, *self.collector.calls()]
        cap = self.budget.max_calls
        if cap is not None:
            planned = sum(u.calls for u in spent) + 1  # this call counts against the cap
            if planned > cap:
                raise BudgetExceeded(f"the next call would cross max_calls: {planned} > {cap}")
        # tokens and cost: what's spent so far (the call's size isn't known before it)
        check_budget(self.budget, spent, self.prices)

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self._check()

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self._check()


_guard: ContextVar[BudgetGuard | None] = ContextVar("measured_ops_budget_guard", default=None)
register_configure_hook(_guard, inheritable=True)


@contextmanager
def guard_budget(
    budget: Budget, prices: Prices, prior: Sequence[Usage], collector: UsageCollector
) -> Iterator[BudgetGuard]:
    """Inside the block, every LangChain model call is checked against `budget` first."""
    guard = BudgetGuard(budget, prices, prior, collector)
    token = _guard.set(guard)
    try:
        yield guard
    finally:
        _guard.reset(token)
