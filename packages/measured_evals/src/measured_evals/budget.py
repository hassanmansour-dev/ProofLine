"""Budget guard: stop a run before it spends past a cap (GROW-15, ADR-0021).

A loop that revises until a score passes can also revise until the money is gone.
A budget is the third exit, after "good enough" and "out of revisions", and the only
one that doesn't depend on a model's opinion.

`check_budget(budget, usages, prices, next_call=...)` is called **before** a model
call. It adds what has been spent to the planned call and raises `BudgetExceeded`
when any cap would be crossed, so the call that breaks the budget is never made.

Rules:
- Caps are on calls, tokens and cost. Any may be left out; at least one is set.
- **Unknown cost fails closed.** With a cost cap, a usage whose model has no price
  can't be shown to be under budget, so it stops the run. A missing price is never
  treated as 0 (the same rule as `measured_evals.cost`).
- The planned call is an estimate from the caller. Output length isn't known before
  a call, so the caller plans with its `max_tokens`: the worst case, not a guess.
- This module counts and compares. It reads no clock, holds no state and calls
  nothing: the caller passes the usages it has recorded.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from measured_evals.cost import Prices, Usage, cost


class BudgetExceeded(RuntimeError):
    """A cap would be crossed. The message names the cap and the numbers, nothing else."""


@dataclass(frozen=True, slots=True)
class Budget:
    """Caps for one run. `None` means no cap of that kind."""

    max_calls: int | None = None
    max_tokens: int | None = None
    max_cost: float | None = None  # same currency as the price table

    def __post_init__(self) -> None:
        caps = (self.max_calls, self.max_tokens, self.max_cost)
        if all(cap is None for cap in caps):
            raise ValueError("a budget needs at least one cap")
        for name, cap in zip(("max_calls", "max_tokens", "max_cost"), caps, strict=True):
            if cap is None:
                continue
            if isinstance(cap, bool) or not isinstance(cap, int | float) or cap < 0:
                raise ValueError(f"{name} must be a non-negative number, got {cap!r}")
        for name in ("max_calls", "max_tokens"):
            cap = getattr(self, name)
            if cap is not None and not isinstance(cap, int):
                raise ValueError(f"{name} must be an int, got {cap!r}")


@dataclass(frozen=True, slots=True)
class Spend:
    calls: int
    tokens: int
    cost: float | None  # None: at least one usage has no known price
    unpriced_models: tuple[str, ...] = ()


def spend(usages: Iterable[Usage], prices: Prices) -> Spend:
    """Total calls, tokens and cost of `usages`. Cost is None if any model is unpriced."""
    calls = tokens = 0
    total: float | None = 0.0
    unpriced: dict[str, None] = {}
    for usage in usages:
        calls += usage.calls
        tokens += usage.tokens
        price = cost(usage, prices)
        if price is None:
            unpriced.setdefault(usage.model)
            total = None
        elif total is not None:
            total += price
    return Spend(calls=calls, tokens=tokens, cost=total, unpriced_models=tuple(unpriced))


def check_budget(
    budget: Budget,
    usages: Iterable[Usage],
    prices: Prices,
    *,
    next_call: Usage | None = None,
) -> Spend:
    """Return the spend including `next_call`, or raise `BudgetExceeded` before it is made."""
    planned = list(usages)
    if next_call is not None:
        planned.append(next_call)
    total = spend(planned, prices)
    when = "the next call would cross" if next_call is not None else "the run has crossed"
    if budget.max_calls is not None and total.calls > budget.max_calls:
        raise BudgetExceeded(f"{when} max_calls: {total.calls} > {budget.max_calls}")
    if budget.max_tokens is not None and total.tokens > budget.max_tokens:
        raise BudgetExceeded(f"{when} max_tokens: {total.tokens} > {budget.max_tokens}")
    if budget.max_cost is not None:
        if total.cost is None:
            raise BudgetExceeded(
                f"cost unknown, so the cost cap can't be checked: no usable price for "
                f"{list(total.unpriced_models)}"
            )
        if total.cost > budget.max_cost:
            raise BudgetExceeded(f"{when} max_cost: {total.cost:.4f} > {budget.max_cost:.4f}")
    return total
