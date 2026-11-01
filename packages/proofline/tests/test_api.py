"""API tests. /health works now; the run flow is the Week 3 S3 target.

Uses create_app(deps=fakes, checkpointer_url="memory://") — never the real module-level app,
so implementing api.py can't accidentally make this suite call paid APIs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from proofline.api import create_app
from proofline.context import Deps
from proofline.schemas import ExtractedFindings, Finding
from tests.fakes import (
    FakeLLM,
    FakeSearch,
    make_critique,
    make_draft,
    make_plan,
    make_result,
    make_settings,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    findings = ExtractedFindings(
        findings=[Finding(claim="Fact.", source_ids=["S1"], confidence="high")]
    )
    llm = FakeLLM().queue(make_plan(), findings, make_draft(), make_critique(9))
    deps = Deps(
        settings=make_settings(tmp_path), llm=llm, search=FakeSearch(default=[make_result(1)])
    )
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


@pytest.mark.xfail(strict=True, reason="Week 3 S3 target")
async def test_run_review_complete_flow(client: AsyncClient) -> None:
    r = await client.post(
        "/runs", json={"topic": "LangGraph checkpointers", "video_format": "short"}
    )
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "awaiting_review"
    assert run["score"] == make_critique(9).overall
    tid = run["thread_id"]

    assert (await client.get(f"/runs/{tid}")).json()["status"] == "awaiting_review"

    r = await client.post(f"/runs/{tid}/review", json={"approved": True})
    assert r.json()["status"] == "completed"
    assert r.json()["final_script"]["thread_id"] == tid

    assert (await client.post(f"/runs/{tid}/review", json={"approved": True})).status_code == 409
    assert (await client.get("/runs/does-not-exist")).status_code == 404
