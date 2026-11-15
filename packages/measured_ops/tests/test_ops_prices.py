"""The demo price table (C7, ADR-0018): bundled, loads, and covers the current models."""

from __future__ import annotations

from pathlib import Path

from measured_evals import ModelPrice
from measured_ops.prices import load_model_prices


def test_demo_table_covers_the_current_models() -> None:
    prices = load_model_prices()
    assert set(prices) == {
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
    }


# platform.claude.com/docs/en/about-claude/pricing, checked 2026-11-14: (input, output,
# cache read, 5m write, 1h write) per MTok; Haiku 5.5 for prompts up to 100,000 tokens.
PUBLISHED = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00, 8.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50, 4.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50, 20.00),
}


def test_demo_table_matches_the_published_prices() -> None:
    prices = load_model_prices()
    for model, (inp, out, read, w5, w1) in PUBLISHED.items():
        p = prices[model]
        assert (p.input, p.output, p.cache_read, p.cache_write_5m, p.cache_write_1h) == (
            inp,
            out,
            read,
            w5,
            w1,
        ), model


def test_demo_cache_writes_follow_the_published_multipliers() -> None:
    for model, p in load_model_prices().items():
        assert p.cache_write_5m == round(p.input * 1.25, 4), model
        assert p.cache_write_1h == round(p.input * 2, 4), model
        assert p.cache_read < p.input, model


def test_loads_a_private_table_from_a_path(tmp_path: Path) -> None:
    path = tmp_path / "prices.toml"
    path.write_text(
        "version = 1\n[models.m]\ninput = 1\noutput = 2\ncache_read = 0.1\n"
        "cache_write_5m = 1.25\ncache_write_1h = 2\n",
        encoding="utf-8",
    )
    assert load_model_prices(path) == {"m": ModelPrice(1, 2, 0.1, 1.25, 2)}


def test_haiku_has_the_published_long_context_tier() -> None:
    tier = load_model_prices()["claude-haiku-5-5"].long_context
    assert tier is not None
    assert tier.above_prompt_tokens == 100_000
    p = tier.price
    assert (p.input, p.output, p.cache_read, p.cache_write_5m, p.cache_write_1h) == (
        0.50,
        2.50,
        0.05,
        0.625,
        1.00,
    )


def test_only_haiku_has_a_long_context_tier() -> None:
    tiered = {m for m, p in load_model_prices().items() if p.long_context is not None}
    assert tiered == {"claude-haiku-5-5"}
