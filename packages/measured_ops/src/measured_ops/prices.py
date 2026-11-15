"""Model price table for cost per agent (C7, ADR-0018).

The demo table ships with this package; a private one (if real prices ever differ)
is passed to `load_model_prices(path)`. Format: `measured_evals.cost.load_prices`.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from measured_evals import ModelPrice, load_prices

DEMO_PRICES = "model_prices.demo.toml"


def load_model_prices(path: Path | None = None) -> dict[str, ModelPrice]:
    """Load a price table. No path = the public demo table bundled with this package."""
    if path is None:
        raw = files("measured_ops").joinpath("config", DEMO_PRICES).read_text(encoding="utf-8")
    else:
        raw = path.read_text(encoding="utf-8")
    return load_prices(raw)
