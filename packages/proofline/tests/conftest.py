from __future__ import annotations

from pathlib import Path

import pytest

from proofline.config import Settings
from tests.fakes import FakeLLM, FakeSearch, make_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_search() -> FakeSearch:
    return FakeSearch()
