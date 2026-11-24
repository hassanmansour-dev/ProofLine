"""The inbox page after approving an executable action (C5): it says what ran."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_inbox_web import PASSPHRASE, Clock, decide, login
from tool_fakes import FakeJira, TriageStubAgent, fake_executors

from measured_ops import ApprovalPolicy, Task, initial_ops_state, load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.inbox.auth import hash_passphrase
from measured_ops.inbox.settings import InboxSettings
from measured_ops.inbox.web import create_inbox_app
from measured_ops.supervisor import build_supervisor
from measured_ops.tools.errors import ToolError

DEMO = load_policy()
POLICY = ApprovalPolicy(version=1, actions={**DEMO.actions, "triage_ticket": "approval"})


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


@pytest.fixture
def jira() -> FakeJira:
    return FakeJira()


@pytest.fixture
def client(tmp_path: Path, jira: FakeJira) -> Iterator[TestClient]:
    settings = InboxSettings(
        passphrase_hash=hash_passphrase(PASSPHRASE, n=2**10),
        session_secret="k" * 40,
        checkpointer_url=f"sqlite:///{tmp_path / 'cp.db'}",
        lock_path=tmp_path / "inbox.lock",
    )
    agents = [TriageStubAgent(jira)]

    async def seed() -> None:
        async with open_checkpointer(settings.checkpointer_url) as saver:
            graph = build_supervisor(agents, POLICY, executors=fake_executors(jira))
            cfg: Any = {"configurable": {"thread_id": "t1"}, "recursion_limit": 40}
            await graph.compile(checkpointer=saver).ainvoke(
                initial_ops_state(Task(agent="ops", goal="Triage.")), cfg
            )

    asyncio.run(seed())
    app = create_inbox_app(settings, agents, POLICY, executors=fake_executors(jira), clock=Clock())
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def test_approving_a_ticket_move_applies_it(client: TestClient, jira: FakeJira) -> None:
    login(client)
    html = client.get("/a/t1").text
    assert "Ticket triage: DEMO-1" in html

    response = decide(client, html, "approve")

    assert response.status_code == 303
    assert "Approved. Applied." in client.get("/").text
    assert jira.issues["DEMO-1"]["status"] == "In Progress"


def test_a_failed_run_says_where_it_is(client: TestClient, jira: FakeJira) -> None:
    login(client)
    html = client.get("/a/t1").text
    jira.fail_next, jira.fail_only, jira.error = 1, {"get_issue"}, ToolError("fake: refused")

    decide(client, html, "approve")

    page = client.get("/").text
    assert "Running it failed: the run is under Stopped" in page
    assert "Stopped (timeout, error or restart)" in page
    assert "/r/t1/retry" in page  # the Retry button
    assert jira.issues["DEMO-1"]["status"] == "To Do"


def test_the_page_says_when_the_kill_switch_stopped_it(client: TestClient, jira: FakeJira) -> None:
    from measured_ops.killswitch import KillSwitch

    login(client)
    html = client.get("/a/t1").text
    KillSwitch.from_env().engage("testing")

    decide(client, html, "approve")

    assert "The kill switch is on, so it did not run" in client.get("/").text
    assert jira.writes == []


def test_the_page_lists_a_run_that_ended_as_failed(tmp_path: Path) -> None:
    # Review 2: a failed result ends the run; without this it left no trace on the page.
    from ops_fakes import ScriptedAgent

    settings = InboxSettings(
        passphrase_hash=hash_passphrase(PASSPHRASE, n=2**10),
        session_secret="k" * 40,
        checkpointer_url=f"sqlite:///{tmp_path / 'cp.db'}",
        lock_path=tmp_path / "inbox.lock",
    )
    agents = [ScriptedAgent(fail=True, name="ops")]

    async def seed() -> None:
        async with open_checkpointer(settings.checkpointer_url) as saver:
            graph = build_supervisor(agents, POLICY).compile(checkpointer=saver)
            cfg: Any = {"configurable": {"thread_id": "f1"}, "recursion_limit": 40}
            await graph.ainvoke(initial_ops_state(Task(agent="ops", goal="Plan the week.")), cfg)

    asyncio.run(seed())
    app = create_inbox_app(settings, agents, POLICY, clock=Clock())
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        login(c)
        page = c.get("/").text

    assert "Failed (last 7 days)" in page
    assert "Plan the week." in page
    assert "ended, nothing more is coming · boom" in page
