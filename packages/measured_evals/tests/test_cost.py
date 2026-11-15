"""Usage and cost (C7, ADR-0018): cache reads and writes priced apart from normal input."""

from __future__ import annotations

import dataclasses

import pytest

from measured_evals.cost import ModelPrice, Usage, cost, load_prices, total_by_agent

PRICES = {
    "model-a": ModelPrice(
        input=4.0, output=20.0, cache_read=0.2, cache_write_5m=5.0, cache_write_1h=8.0
    ),
}


def test_cost_prices_every_token_kind_per_million() -> None:
    usage = Usage(
        agent="content",
        model="model-a",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_tokens=2_000_000,
        cache_write_5m_tokens=100_000,
        cache_write_1h_tokens=10_000,
    )
    # 4 + 10 + 0.4 + 0.5 + 0.08
    assert cost(usage, PRICES) == pytest.approx(14.98)


def test_unknown_model_costs_none_never_zero() -> None:
    assert cost(Usage(agent="a", model="nope", input_tokens=10), PRICES) is None


def test_zero_usage_costs_zero_for_a_known_model() -> None:
    assert cost(Usage(agent="a", model="model-a"), PRICES) == 0.0


@pytest.mark.parametrize(
    "field", ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_5m_tokens"]
)
def test_token_counts_cannot_be_negative(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        Usage(agent="a", model="model-a", **{field: -1})


def test_usage_needs_agent_and_model() -> None:
    with pytest.raises(ValueError, match="agent"):
        Usage(agent="", model="model-a")
    with pytest.raises(ValueError, match="model"):
        Usage(agent="a", model="")


def test_usage_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        Usage(agent="a", model="m").input_tokens = 3  # type: ignore[misc]


def test_prices_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="cache_read"):
        ModelPrice(input=1, output=1, cache_read=-0.1, cache_write_5m=1, cache_write_1h=1)


def test_total_by_agent_sums_tokens_and_cost_per_agent() -> None:
    usages = [
        Usage(agent="content", model="model-a", input_tokens=1_000_000),
        Usage(agent="content", model="model-a", output_tokens=1_000_000),
        Usage(agent="admin", model="model-a", cache_read_tokens=1_000_000),
    ]
    totals = total_by_agent(usages, PRICES)
    assert list(totals) == ["admin", "content"]
    assert totals["content"].tokens == 2_000_000
    assert totals["content"].cost == pytest.approx(24.0)
    assert totals["admin"].cost == pytest.approx(0.2)


def test_total_cost_is_none_if_any_call_has_an_unknown_model() -> None:
    usages = [
        Usage(agent="content", model="model-a", input_tokens=10),
        Usage(agent="content", model="mystery", input_tokens=10),
    ]
    total = total_by_agent(usages, PRICES)["content"]
    assert total.tokens == 20
    assert total.cost is None


def test_load_prices_reads_a_toml_table() -> None:
    raw = """
    version = 1
    [models.model-a]
    input = 4.0
    output = 20.0
    cache_read = 0.2
    cache_write_5m = 5.0
    cache_write_1h = 8.0
    """
    assert load_prices(raw) == PRICES


def test_load_prices_rejects_missing_or_unknown_fields() -> None:
    with pytest.raises(ValueError, match="cache_write_1h"):
        load_prices("version = 1\n[models.m]\ninput=1\noutput=1\ncache_read=1\ncache_write_5m=1\n")
    with pytest.raises(ValueError, match="version"):
        load_prices("version = 2\n")
