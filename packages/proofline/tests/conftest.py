from __future__ import annotations

import os
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


PG_ENV = "MEASURED_OPS_TEST_DATABASE_URL"


@pytest.fixture(params=["sqlite", "postgres"])
def checkpointer_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """Per e2e case. Postgres needs MEASURED_OPS_TEST_DATABASE_URL: skipped locally
    without it, a failure in CI, so CI can never skip it quietly (as in measured_ops)."""
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'cp.db'}"
    url = os.environ.get(PG_ENV)
    if not url:
        if os.environ.get("CI"):
            pytest.fail(f"{PG_ENV} must be set in CI: the Postgres e2e case can't be skipped")
        pytest.skip(f"set {PG_ENV} to run the Postgres case")
    return url
