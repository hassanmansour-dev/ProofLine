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


async def test_nested_blocks_count_only_in_the_innermost_block() -> None:
    # Through a compiled graph, as a sub-agent run inside an agent would be: the inner
    # callback manager inherits the outer collector too, so it must decline.
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        x: int

    model = _model(1)

    async def node(state: S) -> dict[str, int]:
        with collect_usage("inner") as inner_usage:
            await model.ainvoke("a")
        seen["inner"] = inner_usage.totals()
        return {"x": 1}

    seen: dict[str, list[Usage]] = {}
    builder = StateGraph(S)
    builder.add_node("n", node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    graph = builder.compile()

    with collect_usage("outer") as outer:
        await graph.ainvoke({"x": 0})

    assert outer.totals() == []
    assert [(u.agent, u.input_tokens) for u in seen["inner"]] == [("inner", 10)]


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
