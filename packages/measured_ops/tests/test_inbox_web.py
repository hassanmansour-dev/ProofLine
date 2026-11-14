"""The inbox web app (C4, ADR-0017): sign-in, CSRF, escaping, headers, and the approve
/ reject forms, through FastAPI's TestClient over HTTPS against a SQLite checkpoint.

Sync tests on purpose: each run is seeded with asyncio.run() before the app starts,
and TestClient runs the app (and its lifespan) on its own event loop.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from measured_ops import ApprovalPolicy, Task, initial_ops_state, load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.echo import EchoAgent
from measured_ops.inbox.auth import hash_passphrase
from measured_ops.inbox.settings import InboxConfigError, InboxSettings
from measured_ops.inbox.web import create_inbox_app
from measured_ops.supervisor import build_supervisor

PASSPHRASE = "correct horse battery staple"
HOUR = 3600.0


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


@pytest.fixture
def settings(tmp_path: Path) -> InboxSettings:
    return InboxSettings(
        passphrase_hash=hash_passphrase(PASSPHRASE, n=2**10),
        session_secret="k" * 40,
        checkpointer_url=f"sqlite:///{tmp_path / 'cp.db'}",
        lock_path=tmp_path / "inbox.lock",
    )


def seed(url: str, goal: str = "Say why every AI loop needs an exit.", thread: str = "t1") -> None:
    async def run() -> None:
        async with open_checkpointer(url) as saver:
            graph = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=saver)
            cfg: Any = {"configurable": {"thread_id": thread}, "recursion_limit": 40}
            await graph.ainvoke(initial_ops_state(Task(agent="echo", goal=goal)), cfg)

    asyncio.run(run())


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _client(
    settings: InboxSettings, clock: Clock, policy: ApprovalPolicy | None = None
) -> TestClient:
    app = create_inbox_app(settings, [EchoAgent()], policy or load_policy(), clock=clock)
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


@pytest.fixture
def client(settings: InboxSettings, clock: Clock) -> Iterator[TestClient]:
    seed(settings.checkpointer_url)
    with _client(settings, clock) as c:
        yield c


def field(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, f"no hidden field {name!r}"
    return match.group(1)


def login(c: TestClient, passphrase: str = PASSPHRASE) -> Any:
    page = c.get("/login")
    return c.post("/login", data={"passphrase": passphrase, "csrf": field(page.text, "csrf")})


def open_item(c: TestClient, thread: str = "t1") -> str:
    page = c.get(f"/a/{thread}")
    assert page.status_code == 200, page.text
    return page.text


def decide(c: TestClient, html: str, decision: str, **extra: str) -> Any:
    data = {
        "csrf": field(html, "csrf"),
        "interrupt_id": field(html, "interrupt_id"),
        "action_id": field(html, "action_id"),
        "decision": decision,
        "feedback": "",
    }
    data.update(extra)
    return c.post("/a/t1/decision", data=data)


# ---- startup ----------------------------------------------------------------------------


def test_create_refuses_settings_without_secrets(settings: InboxSettings) -> None:
    with pytest.raises(InboxConfigError):
        create_inbox_app(replace(settings, session_secret=""), [EchoAgent()], load_policy())


def test_a_second_instance_refuses_to_start(settings: InboxSettings, clock: Clock) -> None:
    with _client(settings, clock):
        second = _client(settings, clock)
        with pytest.raises(InboxConfigError, match="already running"), second:
            pass


# ---- sign-in ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/"), ("GET", "/a/t1"), ("POST", "/a/t1/decision")]
)
def test_every_page_needs_sign_in(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_wrong_passphrase_is_refused(client: TestClient) -> None:
    response = login(client, "nope")

    assert response.status_code == 401
    assert client.get("/").status_code == 303


def test_right_passphrase_opens_the_list(client: TestClient) -> None:
    response = login(client)

    assert response.status_code == 303
    page = client.get("/")
    assert page.status_code == 200
    assert "Say why every AI loop needs an exit." in page.text
    assert "publish_public" in page.text


def test_session_cookie_is_locked_down(client: TestClient) -> None:
    set_cookie = login(client).headers["set-cookie"].lower()

    assert set_cookie.startswith("__host-mo_inbox=")
    assert "; path=/" in set_cookie
    assert "domain=" not in set_cookie
    assert "; secure" in set_cookie
    assert "; httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert f"max-age={12 * 3600}" in set_cookie


def test_five_failures_lock_sign_in_then_it_reopens(client: TestClient, clock: Clock) -> None:
    for _ in range(5):
        assert login(client, "nope").status_code == 401

    assert login(client).status_code == 429  # even the right passphrase

    clock.t += 15 * 60 + 1
    assert login(client).status_code == 303


def test_session_ends_twelve_hours_after_sign_in(client: TestClient, clock: Clock) -> None:
    login(client)
    clock.t += 11 * HOUR
    assert client.get("/").status_code == 200  # activity doesn't extend it

    clock.t += 1 * HOUR + 1

    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logout_ends_the_session(client: TestClient) -> None:
    login(client)
    page = client.get("/")

    client.post("/logout", data={"csrf": field(page.text, "csrf")})

    assert client.get("/").status_code == 303


def test_parallel_guesses_cant_beat_the_throttle(settings: InboxSettings, clock: Clock) -> None:
    # Review finding: the check ran before the awaited scrypt verify, so concurrent
    # guesses all got through. Sign-in is now serialised.
    app = create_inbox_app(settings, [EchoAgent()], load_policy(), clock=clock)

    async def attack() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            page = await c.get("/login")
            data = {"passphrase": "nope", "csrf": field(page.text, "csrf")}
            responses = await asyncio.gather(*(c.post("/login", data=data) for _ in range(12)))
            return [r.status_code for r in responses]

    codes = asyncio.run(attack())

    assert codes.count(401) == 5
    assert codes.count(429) == 7


def test_logout_revokes_a_copied_cookie(client: TestClient) -> None:
    login(client)
    stolen = dict(client.cookies)
    page = client.get("/")

    client.post("/logout", data={"csrf": field(page.text, "csrf")})
    client.cookies.clear()
    client.cookies.update(stolen)

    assert client.get("/").status_code == 303


def test_oversized_login_forms_are_refused(client: TestClient) -> None:
    page = client.get("/login")
    data = {f"f{i}": "x" for i in range(20)}
    data.update({"passphrase": PASSPHRASE, "csrf": field(page.text, "csrf")})

    assert client.post("/login", data=data).status_code == 400
    assert client.get("/").status_code == 303  # not signed in


# ---- CSRF, escaping, headers ------------------------------------------------------------


def test_posts_without_the_csrf_token_are_refused(client: TestClient) -> None:
    assert client.post("/login", data={"passphrase": PASSPHRASE}).status_code == 403
    login(client)
    html = open_item(client)

    response = decide(client, html, "approve", csrf="forged")

    assert response.status_code == 403
    assert client.get("/a/t1").status_code == 200  # still waiting


def test_preview_is_escaped(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url, goal="<script>alert(1)</script>")
    with _client(settings, clock) as c:
        login(c)
        html = open_item(c)
        listing = c.get("/").text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in listing


def test_security_headers_on_every_page(client: TestClient) -> None:
    for response in (client.get("/login"), client.get("/")):
        headers = response.headers
        assert "default-src 'none'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cache-control"] == "no-store"


def test_pages_are_built_for_a_phone(client: TestClient) -> None:
    page = client.get("/login")

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page.text
    assert client.get("/static/inbox.css").status_code == 200


# ---- approve / reject -------------------------------------------------------------------


def test_approve_clears_the_item(client: TestClient) -> None:
    login(client)
    html = open_item(client)
    assert "Draft post: Say why every AI loop needs an exit." in html

    response = decide(client, html, "approve")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "Nothing is waiting" in client.get("/").text
    assert client.get("/a/t1").status_code == 404


def test_reject_with_feedback_clears_the_item(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    response = decide(client, html, "reject", feedback="Shorter, please.")

    assert response.status_code == 303
    assert "Nothing is waiting" in client.get("/").text


def test_a_second_tap_is_already_decided(client: TestClient) -> None:
    login(client)
    html = open_item(client)
    decide(client, html, "approve")

    response = decide(client, html, "approve")

    assert response.status_code == 409
    assert "already decided" in response.text.lower()


def test_a_refused_answer_shows_why_and_the_item_stays(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    response = decide(client, html, "approve", action_id="someone-else")

    assert response.status_code == 400
    assert "not the action waiting" in response.text
    retry = decide(client, open_item(client), "approve")  # a new answer, a new Command
    assert retry.status_code == 303


def test_an_unknown_decision_value_is_refused(client: TestClient) -> None:
    login(client)

    response = decide(client, open_item(client), "maybe")

    assert response.status_code == 400
    assert client.get("/a/t1").status_code == 200


# ---- a policy tightened to "never" after the pause --------------------------------------


def test_blocked_by_policy_offers_only_reject_and_rejecting_ends_it(
    settings: InboxSettings, clock: Clock
) -> None:
    seed(settings.checkpointer_url)  # paused under the demo policy
    demo = load_policy()
    strict = ApprovalPolicy(version=1, actions={**demo.actions, "publish_public": "never"})
    with _client(settings, clock, policy=strict) as c:
        login(c)
        assert "blocked by policy" in c.get("/").text.lower()
        html = open_item(c)
        assert "blocked by policy" in html.lower()
        assert 'value="approve"' not in html
        assert 'value="reject"' in html

        forced = decide(c, html, "approve")  # crafted by hand: the form has no approve
        assert forced.status_code == 400
        assert "never allowed" in forced.text

        rejected = decide(c, open_item(c), "reject")

        assert rejected.status_code == 303
        assert "Nothing is waiting" in c.get("/").text


def test_a_thread_id_with_a_slash_still_opens(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url, thread="team/run-1")
    with _client(settings, clock) as c:
        login(c)
        listing = c.get("/").text
        assert 'href="/a/team%2Frun-1"' in listing

        html = c.get("/a/team%2Frun-1")

        assert html.status_code == 200
        assert "Say why every AI loop needs an exit." in html.text
