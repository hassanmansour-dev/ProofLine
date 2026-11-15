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


# --- long-context tier (Haiku 5.5: prompts over 100,000 tokens, all input counted) ------

from measured_evals.cost import LongContext  # noqa: E402

SHORT = ModelPrice(
    input=0.10, output=0.50, cache_read=0.01, cache_write_5m=0.125, cache_write_1h=0.20
)
LONG = ModelPrice(
    input=0.50, output=2.50, cache_read=0.05, cache_write_5m=0.625, cache_write_1h=1.00
)
TIERED = {
    "haiku": ModelPrice(
        input=0.10,
        output=0.50,
        cache_read=0.01,
        cache_write_5m=0.125,
        cache_write_1h=0.20,
        long_context=LongContext(above_prompt_tokens=100_000, price=LONG),
    )
}


def _haiku(**tokens: int) -> Usage:
    return Usage(agent="a", model="haiku", **tokens)


def test_prompt_of_exactly_100000_tokens_pays_the_short_rate() -> None:
    usage = _haiku(input_tokens=100_000, output_tokens=1_000)
    assert cost(usage, TIERED) == pytest.approx(cost(usage, {"haiku": SHORT}))


def test_prompt_of_100001_tokens_pays_the_long_rate_on_everything() -> None:
    usage = _haiku(input_tokens=100_001, output_tokens=1_000)
    assert cost(usage, TIERED) == pytest.approx(cost(usage, {"haiku": LONG}))


def test_cache_tokens_count_toward_the_threshold() -> None:
    # 1 uncached + 60,000 read + 40,000 written = 100,001 prompt tokens
    usage = _haiku(input_tokens=1, cache_read_tokens=60_000, cache_write_5m_tokens=40_000)
    assert usage.prompt_tokens == 100_001
    assert cost(usage, TIERED) == pytest.approx(cost(usage, {"haiku": LONG}))


def test_a_total_over_several_calls_on_a_tiered_model_is_unknown_never_underpriced() -> None:
    summed = _haiku(input_tokens=150_000, calls=2)  # could be 2 x 75k or 149k + 1k
    assert cost(summed, TIERED) is None


def test_a_total_over_several_calls_on_an_untiered_model_is_priced() -> None:
    summed = Usage(agent="a", model="model-a", input_tokens=2_000_000, calls=2)
    assert cost(summed, PRICES) == pytest.approx(8.0)


def test_calls_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="calls"):
        _haiku(calls=0)


def test_load_prices_reads_a_long_context_tier() -> None:
    raw = """
    version = 1
    [models.haiku]
    input = 0.10
    output = 0.50
    cache_read = 0.01
    cache_write_5m = 0.125
    cache_write_1h = 0.20
    [models.haiku.long_context]
    above_prompt_tokens = 100000
    input = 0.50
    output = 2.50
    cache_read = 0.05
    cache_write_5m = 0.625
    cache_write_1h = 1.00
    """
    assert load_prices(raw) == TIERED


def test_load_prices_rejects_a_tier_without_its_threshold() -> None:
    raw = (
        "version = 1\n[models.m]\ninput=1\noutput=1\ncache_read=1\ncache_write_5m=1\n"
        "cache_write_1h=1\n[models.m.long_context]\ninput=1\noutput=1\ncache_read=1\n"
        "cache_write_5m=1\ncache_write_1h=1\n"
    )
    with pytest.raises(ValueError, match="above_prompt_tokens"):
        load_prices(raw)
