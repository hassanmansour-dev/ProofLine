from __future__ import annotations

from pathlib import Path

import pytest

from proofline.config import Settings
from tests.fakes import FakeLLM, FakeSearch, make_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _judge_log_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never append to the real judge log (.data/judge_log.jsonl, ADR-0019)."""
    monkeypatch.setenv("MEASURED_OPS_JUDGE_LOG_PATH", str(tmp_path / "judge_log.jsonl"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_search() -> FakeSearch:
    return FakeSearch()
