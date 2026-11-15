"""PII masking for LangSmith traces (C7, ADR-0018). No network: nothing is sent."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.callbacks import CallbackManager
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import utils as ls_utils

from measured_ops.tracing import masked_tracing_callbacks

EMAIL = "jane.doe@example.com"


@pytest.fixture(autouse=True)
def _fresh_env_cache() -> Any:
    # langsmith caches env lookups; each test sets its own
    ls_utils.get_env_var.cache_clear()
    yield
    ls_utils.get_env_var.cache_clear()


@pytest.fixture
def tracing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-not-real")
    monkeypatch.delenv("LANGSMITH_HIDE_INPUTS", raising=False)
    monkeypatch.delenv("LANGSMITH_HIDE_OUTPUTS", raising=False)


def test_no_tracer_when_tracing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING_V2", raising=False)
    assert masked_tracing_callbacks() == []


def _client(tracing_on: None) -> Any:
    [tracer] = masked_tracing_callbacks()
    assert isinstance(tracer, LangChainTracer)
    return tracer.client


def test_inputs_outputs_and_errors_are_masked(tracing_on: None) -> None:
    client = _client(tracing_on)
    state = {"pending_actions": [{"preview": f"Mail {EMAIL}", "target": f"mailto:{EMAIL}"}]}

    for masked in (
        client._hide_run_inputs(state),
        client._hide_run_outputs(state),
        client._hide_run_error(f"ValueError: bad address {EMAIL}"),
    ):
        assert EMAIL not in repr(masked)
        assert "<email>" in repr(masked)


def test_the_masked_tracer_replaces_the_default_one(tracing_on: None) -> None:
    # LangChain adds its env tracer only when no LangChainTracer is present.
    ours = masked_tracing_callbacks()
    assert len(ours) == 1
    manager = CallbackManager.configure(inheritable_callbacks=ours)

    tracers = [h for h in manager.handlers if isinstance(h, LangChainTracer)]
    assert tracers == ours
