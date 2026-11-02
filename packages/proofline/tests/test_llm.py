"""AnthropicLLM adapter: structured-output method and the one repair retry, without the network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from proofline.config import Settings
from proofline.llm import AnthropicLLM, RenderedPrompt, StructuredOutputError, build_llm
from proofline.schemas import Outline
from tests.fakes import make_outline, make_settings

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
    outline = make_outline()
    llm, chat = _llm(settings, [_bad(), {"raw": AIMessage(content="ok"), "parsed": outline}])
    out = await llm.generate(PROMPT, Outline, role="planner", run_config=settings.run_config())
    assert out is outline
    assert chat.kwargs == {"include_raw": True, "method": "json_schema"}
    first, second = chat.runnable.seen
    assert len(second) == len(first) + 2
    assert isinstance(second[-1], HumanMessage)
    assert "missing angle" in str(second[-1].content)


async def test_raises_after_two_invalid_outputs(settings: Settings) -> None:
    llm, _ = _llm(settings, [_bad(), _bad()])
    with pytest.raises(StructuredOutputError, match=r"planner@9\.9\.9"):
        await llm.generate(PROMPT, Outline, role="planner", run_config=settings.run_config())


# ── build_llm: provider + key from the environment ────────────────────────────


def test_build_llm_returns_the_anthropic_adapter_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(build_llm(make_settings(tmp_path)), AnthropicLLM)


def test_build_llm_rejects_an_unknown_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'nope' is not supported; choose one of: anthropic"):
        build_llm(make_settings(tmp_path, llm_provider="nope"))


def test_build_llm_fails_early_without_an_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        build_llm(make_settings(tmp_path))
