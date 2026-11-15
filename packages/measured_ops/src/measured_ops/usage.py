"""Token usage per agent run (C7, ADR-0018), collected from LangChain callbacks.

`with collect_usage(agent_name) as usage:` puts a callback handler in a context
variable that LangChain adds to every callback manager configured inside the block,
so every chat model call in the agent's subgraph reports to it, even when the caller
passes its own config. It does not replace the run's other callbacks (a LangSmith
tracer keeps working), unlike passing `callbacks=[...]` in a config would.

Anthropic's cache tokens: langchain-anthropic's `usage_metadata["input_tokens"]`
**includes** cache reads and writes; `input_token_details` has `cache_read`, and
cache writes either split by TTL (`ephemeral_5m_input_tokens`,
`ephemeral_1h_input_tokens`, with `cache_creation` zeroed) or unsplit
(`cache_creation`, counted here as 5-minute writes, the default TTL). Verified
against langchain-anthropic 1.7.6 (`_create_usage_metadata`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tracers.context import register_configure_hook

from measured_evals import Usage

log = logging.getLogger("measured_ops.usage")

UNKNOWN_MODEL = "unknown"


def _int(d: Mapping[str, Any], key: str) -> int:
    value = d.get(key) or 0
    return int(value)


def usage_from_langchain(agent: str, model: str, usage_metadata: Mapping[str, Any]) -> Usage:
    """Convert one call's `AIMessage.usage_metadata` to a `Usage` (uncached input apart)."""
    details: Mapping[str, Any] = usage_metadata.get("input_token_details") or {}
    cache_read = _int(details, "cache_read")
    write_1h = _int(details, "ephemeral_1h_input_tokens")
    write_5m = _int(details, "ephemeral_5m_input_tokens") + _int(details, "cache_creation")
    total_in = _int(usage_metadata, "input_tokens")
    uncached = total_in - cache_read - write_5m - write_1h
    if uncached < 0:
        log.warning(
            "usage: %s on %s reported more cache tokens than input tokens; counting 0 uncached",
            agent,
            model,
        )
        uncached = 0
    return Usage(
        agent=agent,
        model=model,
        input_tokens=uncached,
        output_tokens=_int(usage_metadata, "output_tokens"),
        cache_read_tokens=cache_read,
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
    )


class UsageCollector(BaseCallbackHandler):
    """Collects usage for one agent run. Calls without a model name count as "unknown"
    (priced as unknown, never free); calls without usage are logged, not dropped silently."""

    run_inline = True  # record in the caller's thread; no executor hop

    def __init__(self, agent: str) -> None:
        super().__init__()
        self.agent = agent
        self._lock = threading.Lock()
        self._models: dict[UUID, str] = {}
        self._calls: list[Usage] = []

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = (metadata or {}).get("ls_model_name")
        if isinstance(name, str) and name:
            with self._lock:
                self._models[run_id] = name

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            fallback = self._models.pop(run_id, None)
        generation = (
            response.generations[0][0] if response.generations and response.generations[0] else None
        )
        message = generation.message if isinstance(generation, ChatGeneration) else None
        if not isinstance(message, AIMessage) or message.usage_metadata is None:
            log.warning("usage: a model call by %s reported no usage; not counted", self.agent)
            return
        model = (
            message.response_metadata.get("model_name")
            or (response.llm_output or {}).get("model_name")
            or fallback
            or UNKNOWN_MODEL
        )
        usage = usage_from_langchain(self.agent, str(model), dict(message.usage_metadata))
        with self._lock:
            self._calls.append(usage)

    def totals(self) -> list[Usage]:
        """One `Usage` per model, summed, in first-seen order."""
        sums: dict[str, Usage] = {}
        with self._lock:
            calls = list(self._calls)
        for u in calls:
            prev = sums.get(u.model)
            sums[u.model] = (
                u
                if prev is None
                else Usage(
                    agent=u.agent,
                    model=u.model,
                    input_tokens=prev.input_tokens + u.input_tokens,
                    output_tokens=prev.output_tokens + u.output_tokens,
                    cache_read_tokens=prev.cache_read_tokens + u.cache_read_tokens,
                    cache_write_5m_tokens=prev.cache_write_5m_tokens + u.cache_write_5m_tokens,
                    cache_write_1h_tokens=prev.cache_write_1h_tokens + u.cache_write_1h_tokens,
                )
            )
        return list(sums.values())


_current: ContextVar[UsageCollector | None] = ContextVar("measured_ops_usage", default=None)
register_configure_hook(_current, inheritable=True)  # once, at import


@contextmanager
def collect_usage(agent: str) -> Iterator[UsageCollector]:
    """Count every chat model call made inside the block (and its tasks) for `agent`."""
    collector = UsageCollector(agent)
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)
