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
