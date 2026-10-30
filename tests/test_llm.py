"""AnthropicLLM adapter: structured-output method and the one repair retry, without the network."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from proofline.config import Settings
from proofline.llm import AnthropicLLM, RenderedPrompt, StructuredOutputError
from proofline.schemas import Plan
from tests.fakes import make_plan

pytestmark = pytest.mark.anyio

PROMPT = RenderedPrompt(name="planner", version="9.9.9", system="sys", user="user")


class FakeRunnable:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.seen: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list[BaseMessage], config: Any = None) -> dict[str, Any]:
        self.seen.append(list(messages))
        return self.results.pop(0)


class FakeChat:
    def __init__(self, runnable: FakeRunnable) -> None:
        self.runnable = runnable
        self.kwargs: dict[str, Any] = {}

    def with_structured_output(self, schema: type, **kwargs: Any) -> FakeRunnable:
        self.kwargs = kwargs
        return self.runnable


def _llm(settings: Settings, results: list[dict[str, Any]]) -> tuple[AnthropicLLM, FakeChat]:
    llm = AnthropicLLM()
    chat = FakeChat(FakeRunnable(results))
    llm._clients[settings.run_config().models["planner"]] = chat  # type: ignore[assignment]
    return llm, chat


def _bad() -> dict[str, Any]:
    return {"raw": AIMessage(content="{}"), "parsed": None, "parsing_error": "missing angle"}


async def test_uses_json_schema_and_repairs_once(settings: Settings) -> None:
    plan = make_plan()
    llm, chat = _llm(settings, [_bad(), {"raw": AIMessage(content="ok"), "parsed": plan}])
    out = await llm.generate(PROMPT, Plan, role="planner", run_config=settings.run_config())
    assert out is plan
    assert chat.kwargs == {"include_raw": True, "method": "json_schema"}
    first, second = chat.runnable.seen
    assert len(second) == len(first) + 2
    assert isinstance(second[-1], HumanMessage)
    assert "missing angle" in str(second[-1].content)


async def test_raises_after_two_invalid_outputs(settings: Settings) -> None:
    llm, _ = _llm(settings, [_bad(), _bad()])
    with pytest.raises(StructuredOutputError, match=r"planner@9\.9\.9"):
        await llm.generate(PROMPT, Plan, role="planner", run_config=settings.run_config())
