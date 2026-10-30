"""StructuredLLM port + Anthropic adapter (ADR-0003).

Nodes call `deps.llm.generate(prompt, Schema, role=..., run_config=...)` and get a
validated Pydantic object back. The adapter owns model selection, tracing tags,
and one repair retry when the model's output doesn't validate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from proofline.schemas import ModelRole, ModelSpec, RunConfig

log = logging.getLogger("proofline.llm")

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    system: str
    user: str

    @property
    def tag(self) -> str:
        return f"{self.name}@{self.version}"


class StructuredLLM(Protocol):
    async def generate(
        self,
        prompt: RenderedPrompt,
        schema: type[T],
        *,
        role: ModelRole,
        run_config: RunConfig,
    ) -> T: ...


class StructuredOutputError(RuntimeError):
    pass


class AnthropicLLM:
    """Production adapter. LangSmith traces automatically when LANGSMITH_TRACING=true."""

    def __init__(self, *, max_retries: int = 2) -> None:
        self._max_retries = max_retries
        self._clients: dict[ModelSpec, ChatAnthropic] = {}

    def _client(self, spec: ModelSpec) -> ChatAnthropic:
        if spec not in self._clients:
            self._clients[spec] = ChatAnthropic(
                model=spec.model,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                max_retries=self._max_retries,
            )
        return self._clients[spec]

    async def generate(
        self,
        prompt: RenderedPrompt,
        schema: type[T],
        *,
        role: ModelRole,
        run_config: RunConfig,
    ) -> T:
        spec = run_config.models[role]
        runnable = self._client(spec).with_structured_output(schema, include_raw=True)
        messages: list[BaseMessage] = [
            SystemMessage(content=prompt.system),
            HumanMessage(content=prompt.user),
        ]
        config = RunnableConfig(
            run_name=prompt.tag,
            tags=[f"role:{role}", f"prompt:{prompt.tag}"],
            metadata={
                "prompt_name": prompt.name,
                "prompt_version": prompt.version,
                "model": spec.model,
            },
        )

        for attempt in (1, 2):
            result = cast(dict[str, Any], await runnable.ainvoke(messages, config=config))
            parsed = result.get("parsed")
            if isinstance(parsed, schema):
                return parsed
            error = result.get("parsing_error")
            log.warning("structured output failed (%s, attempt %d): %s", prompt.tag, attempt, error)
            raw = result.get("raw")
            if isinstance(raw, AIMessage):
                messages = [
                    *messages,
                    raw,
                    HumanMessage(
                        content=(
                            "Your previous output did not validate against the required schema. "
                            f"Error: {error}. Return a corrected output with every required field."
                        )
                    ),
                ]
        raise StructuredOutputError(f"{prompt.tag}: no valid {schema.__name__} after 2 attempts")
