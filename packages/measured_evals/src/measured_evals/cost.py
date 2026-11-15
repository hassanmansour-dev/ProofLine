"""Token usage and cost per agent (C7, ADR-0018).

Provider-neutral: callers convert their SDK's usage object to `Usage`. Prompt-cache
tokens are their own fields because they're priced differently from normal input:
`input_tokens` counts **uncached** input only, never cache reads or writes.

Prices are per million tokens and passed in (a demo table ships with the app; real
prices live in private config if they differ). A model missing from the table costs
`None`, shown as "unknown", never 0: an unpriced call must not look free.

Some models price by prompt length (Claude Haiku 5.5: a request whose prompt is over
100,000 tokens, counting cache reads and writes, pays higher prices on everything).
`ModelPrice.long_context` models that tier, and it is decided **per call**: price one
`Usage` per call. A total over several calls on a tiered model costs `None`, because
it can't tell which calls crossed the threshold, and a guess could under-price.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields

PER = 1_000_000


@dataclass(frozen=True, slots=True)
class Usage:
    """Tokens used by one LLM call (or a sum of calls) for one agent and model."""

    agent: str
    model: str
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    calls: int = 1  # model calls summed into this record

    def __post_init__(self) -> None:
        if isinstance(self.calls, bool) or not isinstance(self.calls, int) or self.calls < 1:
            raise ValueError(f"calls must be a positive int, got {self.calls!r}")
        if not self.agent:
            raise ValueError("usage needs an agent name")
        if not self.model:
            raise ValueError("usage needs a model name")
        for f in fields(self):
            if f.name.endswith("_tokens"):
                value = getattr(self, f.name)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{f.name} must be a non-negative int, got {value!r}")

    @property
    def prompt_tokens(self) -> int:
        """All input the model read: uncached input plus cache reads and writes."""
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )

    @property
    def tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """US dollars per million tokens, with an optional higher tier for long prompts."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float
    long_context: LongContext | None = None

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name == "long_context":
                continue
            value = getattr(self, f.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or math.isnan(value)
                or value < 0
            ):
                raise ValueError(f"price {f.name} must be a non-negative number, got {value!r}")


@dataclass(frozen=True, slots=True)
class LongContext:
    """Prices for a call whose prompt (all input, cache included) is over the threshold."""

    above_prompt_tokens: int
    price: ModelPrice

    def __post_init__(self) -> None:
        n = self.above_prompt_tokens
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"above_prompt_tokens must be a positive int, got {n!r}")
        if self.price.long_context is not None:
            raise ValueError("a long-context tier can't have its own tier")


Prices = Mapping[str, ModelPrice]


def cost(usage: Usage, prices: Prices) -> float | None:
    """Dollar cost of `usage`, or None if its model has no price, or if `usage` sums
    several calls on a model whose price depends on each call's prompt length."""
    price = prices.get(usage.model)
    if price is None:
        return None
    tier = price.long_context
    if tier is not None:
        if usage.calls != 1:
            return None  # can't tell which calls crossed the threshold: never under-price
        if usage.prompt_tokens > tier.above_prompt_tokens:
            price = tier.price
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_5m_tokens * price.cache_write_5m
        + usage.cache_write_1h_tokens * price.cache_write_1h
    ) / PER


@dataclass(frozen=True, slots=True)
class AgentTotal:
    tokens: int
    cost: float | None  # None if any call's model has no price


def total_by_agent(usages: Iterable[Usage], prices: Prices) -> dict[str, AgentTotal]:
    """Tokens and cost per agent, agents sorted by name."""
    tokens: dict[str, int] = {}
    costs: dict[str, float | None] = {}
    for u in usages:
        tokens[u.agent] = tokens.get(u.agent, 0) + u.tokens
        c = cost(u, prices)
        prev = costs.get(u.agent, 0.0)
        costs[u.agent] = None if c is None or prev is None else prev + c
    return {a: AgentTotal(tokens[a], costs[a]) for a in sorted(tokens)}


_PRICE_FIELDS = {f.name for f in fields(ModelPrice)} - {"long_context"}


def _price_row(where: str, row: object, extra_allowed: set[str]) -> dict[str, object]:
    if not isinstance(row, dict):
        raise ValueError(f"{where} must be a table")
    missing = _PRICE_FIELDS - set(row)
    extra = set(row) - _PRICE_FIELDS - extra_allowed
    if missing or extra:
        raise ValueError(f"{where}: missing {sorted(missing)}, unknown {sorted(extra)}")
    return row


def load_prices(toml_text: str) -> dict[str, ModelPrice]:
    """Parse a price table: `version = 1`, then `[models.<id>]` with every price field."""
    raw = tomllib.loads(toml_text)
    if raw.get("version") != 1:
        raise ValueError(f"price table version must be 1, got {raw.get('version')!r}")
    unknown_top = set(raw) - {"version", "models"}
    if unknown_top:
        raise ValueError(f"unknown keys in price table: {sorted(unknown_top)}")
    out: dict[str, ModelPrice] = {}
    for model, row in raw.get("models", {}).items():
        base = dict(_price_row(f"[models.{model}]", row, {"long_context"}))
        tier_row = base.pop("long_context", None)
        tier = None
        if tier_row is not None:
            where = f"[models.{model}.long_context]"
            t = dict(_price_row(where, tier_row, {"above_prompt_tokens"}))
            if "above_prompt_tokens" not in t:
                raise ValueError(f"{where}: missing ['above_prompt_tokens']")
            above = t.pop("above_prompt_tokens")
            tier = LongContext(above_prompt_tokens=above, price=ModelPrice(**t))  # type: ignore[arg-type]
        out[model] = ModelPrice(**base, long_context=tier)  # type: ignore[arg-type]
    return out
