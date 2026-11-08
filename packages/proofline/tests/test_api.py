"""API tests: /health only. Run and review endpoints were dropped (ADR-0014).

Uses create_app(deps=fakes, checkpointer_url="memory://") — never the real module-level app,
so implementing api.py can't accidentally make this suite call paid APIs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from proofline.api import create_app
from proofline.schemas import ExtractedFindings, Finding
from tests.fakes import (
    FakeLLM,
    FakeSearch,
    make_critique,
    make_deps,
    make_draft,
    make_outline,
    make_result,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    findings = ExtractedFindings(
        findings=[Finding(claim="Fact.", source_ids=["S1"], confidence="high")]
    )
    llm = FakeLLM().queue(make_outline(), findings, make_draft(), make_critique(9))
    deps = make_deps(tmp_path, llm, FakeSearch(default=[make_result(1)]))
    app = create_app(deps=deps, checkpointer_url="memory://")
    # ASGITransport doesn't run lifespan itself, so enter it explicitly.
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checkpointer": "memory"}
