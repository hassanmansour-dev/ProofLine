"""The usage collector (C7, ADR-0018): scoped to one agent run, sums per model."""

from __future__ import annotations

import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from measured_evals import Usage
from measured_ops.usage import collect_usage, usage_from_langchain

pytestmark = pytest.mark.anyio


def _model(n: int) -> GenericFakeChatModel:
    reply = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        response_metadata={"model_name": "model-a"},
    )
    return GenericFakeChatModel(messages=iter([reply] * n))


async def test_collects_only_inside_the_block() -> None:
    model = _model(3)
    with collect_usage("content") as usage:
        await model.ainvoke("a")
        await model.ainvoke("b")
    await model.ainvoke("c")  # outside: not counted

    assert usage.totals() == [
        Usage(agent="content", model="model-a", input_tokens=20, output_tokens=4)
    ]


async def test_nested_blocks_count_once_in_the_inner_block() -> None:
    model = _model(1)
    with collect_usage("outer") as outer, collect_usage("inner") as inner:
        await model.ainvoke("a")

    assert outer.totals() == []
    assert [u.agent for u in inner.totals()] == ["inner"]


def test_inconsistent_counts_are_clamped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    usage = usage_from_langchain(
        "a",
        "m",
        {
            "input_tokens": 5,
            "output_tokens": 0,
            "total_tokens": 5,
            "input_token_details": {"cache_read": 9},
        },
    )
    assert usage.input_tokens == 0
    assert usage.cache_read_tokens == 9
    assert "more cache tokens than input tokens" in caplog.text
