from __future__ import annotations

import os
from pathlib import Path

import pytest

PG_ENV = "MEASURED_OPS_TEST_DATABASE_URL"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["sqlite", "postgres"])
def checkpointer_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """A checkpointer URL per e2e case. Postgres needs MEASURED_OPS_TEST_DATABASE_URL:
    skipped locally without it, a failure in CI, so CI can never skip it quietly."""
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'cp.db'}"
    url = os.environ.get(PG_ENV)
    if not url:
        if os.environ.get("CI"):
            pytest.fail(f"{PG_ENV} must be set in CI: the Postgres e2e case can't be skipped")
        pytest.skip(f"set {PG_ENV} to run the Postgres case")
    return url
