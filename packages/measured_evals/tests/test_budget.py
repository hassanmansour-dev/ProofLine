"""Budget guard (GROW-15, ADR-0021). Prices and usages here are made up for the arithmetic."""

from __future__ import annotations

import pytest

from measured_evals.budget import Budget, BudgetExceeded, check_budget, spend
from measured_evals.cost import LongContext, ModelPrice, Usage

PRICE = ModelPrice(input=4.0, output=20.0, cache_read=0.2, cache_write_5m=5.0, cache_write_1h=8.0)
PRICES = {"model-a": PRICE}


def _call(
    *, model: str = "model-a", input_tokens: int = 100_000, output_tokens: int = 10_000
) -> Usage:
    # on model-a: 0.4 + 0.2 = 0.60 per call, 110_000 tokens
    return Usage(
        agent="content", model=model, input_tokens=input_tokens, output_tokens=output_tokens
    )


def test_spend_adds_calls_tokens_and_cost() -> None:
    total = spend([_call(), _call()], PRICES)
    assert (total.calls, total.tokens) == (2, 220_000)
    assert total.cost == pytest.approx(1.20)
    assert total.unpriced_models == ()


def test_nothing_spent_is_zero_not_unknown() -> None:
    total = spend([], PRICES)
    assert (total.calls, total.tokens, total.cost) == (0, 0, 0.0)


def test_under_every_cap_returns_the_spend() -> None:
    budget = Budget(max_calls=3, max_tokens=400_000, max_cost=2.0)
    total = check_budget(budget, [_call(), _call()], PRICES, next_call=_call())
    assert total.calls == 3
    assert total.cost == pytest.approx(1.80)


@pytest.mark.parametrize(
    ("budget", "cap"),
    [
        (Budget(max_calls=2), "max_calls: 3 > 2"),
        (Budget(max_tokens=300_000), "max_tokens: 330000 > 300000"),
        (Budget(max_cost=1.5), "max_cost: 1.8000 > 1.5000"),
    ],
)
def test_the_call_that_would_cross_a_cap_is_stopped_before_it_is_made(
    budget: Budget, cap: str
) -> None:
    with pytest.raises(BudgetExceeded, match="the next call would cross " + cap):
        check_budget(budget, [_call(), _call()], PRICES, next_call=_call())
    # the same history without the planned call is fine
    check_budget(budget, [_call(), _call()], PRICES)


def test_exactly_at_the_cap_is_allowed() -> None:
    check_budget(Budget(max_calls=2, max_cost=1.20), [_call()], PRICES, next_call=_call())


def test_a_run_already_over_says_so() -> None:
    with pytest.raises(BudgetExceeded, match="the run has crossed max_calls"):
        check_budget(Budget(max_calls=1), [_call(), _call()], PRICES)


def test_unknown_price_fails_closed_under_a_cost_cap() -> None:
    usages = [_call(), _call(model="unlisted-model")]
    assert spend(usages, PRICES).cost is None
    with pytest.raises(BudgetExceeded, match=r"cost unknown.*unlisted-model"):
        check_budget(Budget(max_cost=100.0), usages, PRICES)


def test_unknown_price_does_not_block_a_budget_without_a_cost_cap() -> None:
    total = check_budget(Budget(max_calls=5), [_call(model="unlisted-model")], PRICES)
    assert total.cost is None
    assert total.unpriced_models == ("unlisted-model",)


def test_summed_usage_on_a_tiered_model_is_unknown_cost_so_it_fails_closed() -> None:
    tiered = {
        "model-t": ModelPrice(
            input=4.0,
            output=20.0,
            cache_read=0.2,
            cache_write_5m=5.0,
            cache_write_1h=8.0,
            long_context=LongContext(above_prompt_tokens=200_000, price=PRICE),
        )
    }
    summed = Usage(agent="content", model="model-t", input_tokens=10, output_tokens=10, calls=2)
    with pytest.raises(BudgetExceeded, match="cost unknown"):
        check_budget(Budget(max_cost=1.0), [summed], tiered)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"max_calls": -1},
        {"max_calls": True},
        {"max_calls": 2.5},
        {"max_tokens": 1.0},
        {"max_cost": -0.01},
        {"max_cost": "5"},
    ],
)
def test_a_bad_budget_is_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"budget|must be"):
        Budget(**kwargs)  # type: ignore[arg-type]


def test_a_zero_budget_stops_the_first_call() -> None:
    with pytest.raises(BudgetExceeded):
        check_budget(Budget(max_calls=0), [], PRICES, next_call=_call())
