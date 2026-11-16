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


def wait_for_item(c: TestClient, thread: str = "t1", seconds: float = 5.0) -> str:
    """Background revisions (ADR-0019): poll until the run waits at the gate again."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page = c.get(f"/a/{thread}")
        if page.status_code == 200:
            return page.text
        time.sleep(0.02)
    raise AssertionError(f"run {thread} didn't come back to the inbox in {seconds}s")


def decide(c: TestClient, html: str, decision: str, *, thread: str = "t1", **extra: str) -> Any:
    data = {
        "csrf": field(html, "csrf"),
        "interrupt_id": field(html, "interrupt_id"),
        "action_id": field(html, "action_id"),
        "decision": decision,
        "feedback": "",
    }
    data.update(extra)
    return c.post(f"/a/{thread}/decision", data=data)


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


def test_reject_without_feedback_clears_the_item(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    response = decide(client, html, "reject")

    assert response.status_code == 303
    assert "Nothing is waiting" in client.get("/").text


def test_reject_with_feedback_brings_back_a_revised_item(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    response = decide(client, html, "revise", feedback="Shorter, please.")

    assert response.status_code == 303
    again = wait_for_item(client)
    assert field(again, "action_id") != field(html, "action_id")


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


def test_login_answers_head_for_the_runbook_check(client: TestClient) -> None:
    # docs/INBOX.md checks the server with `curl -I .../login`, which sends HEAD.
    response = client.head("/login")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-frame-options"] == "DENY"


# ---- checks and cost on each waiting action (C7, ADR-0018) -------------------------------

LEAKY_GOAL = "Mail jane.doe@example.com and see https://example.com/post?ref=7"


def test_detail_shows_checks_with_plain_reasons(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url, goal=LEAKY_GOAL)
    with _client(settings, clock) as c:
        login(c)
        html = open_item(c)

    assert "link with query parameters" in html
    assert "email found" in html
    assert "a failed check means this action needs your approval" in html
    assert "No model calls." in html


def test_list_marks_a_failed_check(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url, goal=LEAKY_GOAL)
    with _client(settings, clock) as c:
        login(c)
        html = c.get("/").text

    assert "Check failed" in html


def test_clean_action_shows_passed_checks_and_no_badge(client: TestClient) -> None:
    login(client)
    listing = client.get("/").text
    html = open_item(client)

    assert "Check failed" not in listing
    assert html.count(">passed<") == 5  # 3 on the preview, 2 on the target


def test_pii_value_appears_only_inside_the_preview(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url, goal="Draft for jane.doe@example.com")
    with _client(settings, clock) as c:
        login(c)
        listing = c.get("/").text
        html = open_item(c)

    # The demo EchoAgent copies the goal into the preview, so both carry the address by
    # construction. Everything else on the page (checks, reasons, cost) must not.
    rest = re.sub(r'<pre class="preview">.*?</pre>', "", html, flags=re.DOTALL)
    rest = re.sub(r"<h1>.*?</h1>", "", rest, flags=re.DOTALL)
    assert "jane.doe@example.com" in html  # the human sees what they approve
    assert "email found" in rest
    assert "jane.doe" not in rest
    assert "Check failed" in listing


# ---- background revisions on the page (C8, ADR-0019) ------------------------------------


def test_reject_with_feedback_says_it_is_revising(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    decide(client, html, "revise", feedback="Shorter, please.")
    listing = client.get("/").text

    assert "Revising" in listing
    wait_for_item(client)


def test_list_shows_runs_in_progress_and_retry_for_stopped_ones(
    settings: InboxSettings, clock: Clock
) -> None:
    from measured_ops.inbox.service import RunStatus

    seed(settings.checkpointer_url)
    with _client(settings, clock) as c:
        login(c)

        async def fake_in_progress() -> list[RunStatus]:
            return [
                RunStatus("r1", "echo", "Running goal.", "running"),
                RunStatus("r2", "echo", "Queued goal.", "queued"),
                RunStatus("r3", "echo", "Stopped goal.", "stopped"),
                RunStatus("r4", "echo", "Elsewhere goal.", "elsewhere"),
            ]

        c.app.state.inbox.in_progress = fake_in_progress  # type: ignore[attr-defined]
        html = c.get("/").text

    for text in ("Revising…", "Revision queued", "Revision stopped", "In progress elsewhere"):
        assert text in html
    assert html.count('action="/r/') == 1  # Retry only on the stopped run
    assert 'action="/r/r3/retry"' in html


def test_retry_continues_a_stopped_run(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url)
    with _client(settings, clock) as c:
        login(c)
        calls: list[str] = []

        async def fake_retry(thread_id: str) -> None:
            calls.append(thread_id)

        c.app.state.inbox.retry = fake_retry  # type: ignore[attr-defined]
        page = c.get("/")
        response = c.post("/r/r3/retry", data={"csrf": field(page.text, "csrf")})

    assert response.status_code == 303
    assert calls == ["r3"]


def test_retry_needs_the_csrf_token(client: TestClient) -> None:
    login(client)
    assert client.post("/r/r3/retry", data={"csrf": "wrong"}).status_code == 403


def test_retry_of_a_run_that_is_not_stopped_says_so(client: TestClient) -> None:
    login(client)
    page = client.get("/")
    response = client.post("/r/t1/retry", data={"csrf": field(page.text, "csrf")})
    assert response.status_code == 409
    assert "Not stopped" in response.text


def test_revision_timeout_comes_from_the_environment() -> None:
    env = {
        "MEASURED_OPS_INBOX_PASSPHRASE_HASH": hash_passphrase(PASSPHRASE, n=2**10),
        "MEASURED_OPS_INBOX_SESSION_SECRET": "k" * 40,
        "MEASURED_OPS_INBOX_REVISION_TIMEOUT_S": "120",
    }
    assert InboxSettings.from_env(env).revision_timeout_s == 120.0
    assert (
        InboxSettings.from_env(
            {**env, "MEASURED_OPS_INBOX_REVISION_TIMEOUT_S": ""}
        ).revision_timeout_s
        == 600.0
    )


def test_lifespan_starts_recovery_and_closes_revisions(
    settings: InboxSettings, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from measured_ops.inbox import service

    seen: list[str] = []
    monkeypatch.setattr(service.Inbox, "start", lambda self: _note(seen, "start"))
    monkeypatch.setattr(service.Inbox, "aclose", lambda self: _note(seen, "aclose"))
    with _client(settings, clock):
        assert seen == ["start"]
    assert seen == ["start", "aclose"]


async def _note(seen: list[str], what: str) -> None:
    seen.append(what)


@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf"])
def test_a_bad_revision_timeout_is_refused(bad: str) -> None:
    from measured_ops.inbox.settings import InboxConfigError

    env = {
        "MEASURED_OPS_INBOX_PASSPHRASE_HASH": hash_passphrase(PASSPHRASE, n=2**10),
        "MEASURED_OPS_INBOX_SESSION_SECRET": "k" * 40,
        "MEASURED_OPS_INBOX_REVISION_TIMEOUT_S": bad,
    }
    with pytest.raises(InboxConfigError, match="REVISION_TIMEOUT_S"):
        InboxSettings.from_env(env)


# ---- explicit reject: revise or stop (C8, ADR-0020) ---------------------------------------


def test_the_form_has_approve_revise_and_reject_and_stop(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    assert 'value="approve"' in html
    assert ">Revise with this feedback<" in html
    assert 'data-needs-feedback="feedback"' in html  # inbox.js disables it while empty
    assert ">Reject and stop<" in html
    assert '<script src="/static/inbox.js" defer></script>' in html
    assert "First draft · up to 3 revisions" in html


def test_revise_with_an_empty_box_is_refused_and_the_run_waits(client: TestClient) -> None:
    login(client)
    html = open_item(client)

    response = decide(client, html, "revise", feedback="   ")

    assert response.status_code == 400
    assert "write what to change" in response.text
    assert field(open_item(client), "action_id") == field(html, "action_id")


def test_reject_and_stop_ignores_the_text_and_never_revises(
    settings: InboxSettings, clock: Clock
) -> None:
    seed(settings.checkpointer_url)
    with _client(settings, clock) as c:
        login(c)
        html = open_item(c)

        response = decide(c, html, "reject", feedback="This text must not revise anything.")

        assert response.status_code == 303
        assert "Nothing is waiting" in c.get("/").text
        assert "In progress" not in c.get("/").text  # no revision queued or running
    state = _thread_state(settings.checkpointer_url, "t1")
    [decision] = state.values["decisions"]
    assert (decision.approved, decision.feedback) == (False, None)
    assert state.next == ()
    assert state.values["task"].inputs.get("revision", 0) == 0


def test_the_page_shows_the_revision_number(client: TestClient) -> None:
    login(client)
    decide(client, open_item(client), "revise", feedback="Shorter.")

    again = wait_for_item(client)

    assert "Revision 1 of 3" in again


def test_with_no_revisions_left_revise_is_refused(client: TestClient) -> None:
    login(client)
    for _ in range(3):
        decide(
            client,
            open_item(client) if _ == 0 else wait_for_item(client),
            "revise",
            feedback="Again.",
        )
    html = wait_for_item(client)
    assert "Revision 3 of 3" in html
    assert "No revisions left" in html

    response = decide(client, html, "revise", feedback="Once more.")

    assert response.status_code == 400
    assert "no revisions left" in response.text


def test_inbox_js_is_served_and_the_csp_allows_only_self_scripts(client: TestClient) -> None:
    js = client.get("/static/inbox.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")
    csp = client.get("/login").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp


def _thread_state(url: str, thread_id: str) -> Any:
    async def read() -> Any:
        async with open_checkpointer(url) as saver:
            graph = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=saver)
            return await graph.aget_state({"configurable": {"thread_id": thread_id}})

    return asyncio.run(read())


# ---- ready to paste (C8, ADR-0020) ---------------------------------------------------------


def seed_draft(
    url: str, goal: str = "Say why every AI loop needs an exit.", thread: str = "d1"
) -> None:
    async def run() -> None:
        async with open_checkpointer(url) as saver:
            graph = build_supervisor([EchoAgent("approve_draft")], load_policy()).compile(
                checkpointer=saver
            )
            cfg: Any = {"configurable": {"thread_id": thread}, "recursion_limit": 40}
            await graph.ainvoke(initial_ops_state(Task(agent="echo", goal=goal)), cfg)

    asyncio.run(run())


def _approve_draft(c: TestClient, thread: str = "d1") -> None:
    assert decide(c, open_item(c, thread), "approve", thread=thread).status_code == 303


def test_ready_needs_sign_in(client: TestClient) -> None:
    assert client.get("/ready").status_code in (303, 307)


def test_an_approved_draft_is_ready_to_paste_with_copy_buttons(
    settings: InboxSettings, clock: Clock
) -> None:
    seed_draft(settings.checkpointer_url, goal="Exits <b>matter</b>")
    with _client(settings, clock) as c:
        login(c)
        _approve_draft(c)
        html = c.get("/ready").text

    assert "Ready to paste" in html
    assert "Draft post: Exits &lt;b&gt;matter&lt;/b&gt;" in html  # escaped, in a text box
    assert "<b>matter</b>" not in html
    assert 'data-copy="block-0-0"' in html
    assert '<textarea id="block-0-0" readonly' in html
    assert ">Mark as posted<" in html


def test_ready_lists_newest_first(settings: InboxSettings, clock: Clock) -> None:
    seed_draft(settings.checkpointer_url, goal="Older goal.", thread="d1")
    seed_draft(settings.checkpointer_url, goal="Newer goal.", thread="d2")
    with _client(settings, clock) as c:
        login(c)
        _approve_draft(c, "d1")
        _approve_draft(c, "d2")
        html = c.get("/ready").text

    assert html.index("Newer goal.") < html.index("Older goal.")


def test_mark_as_posted_hides_it_and_never_resumes_a_run(
    settings: InboxSettings, clock: Clock
) -> None:
    seed_draft(settings.checkpointer_url)
    with _client(settings, clock) as c:
        login(c)
        _approve_draft(c)
        page = c.get("/ready").text
        action_id = re.search(r'action="/ready/([^/"]+)/posted"', page).group(1)  # type: ignore[union-attr]
        before = _thread_state(settings.checkpointer_url, "d1")

        response = c.post(f"/ready/{action_id}/posted", data={"csrf": field(page, "csrf")})

        assert response.status_code == 303
        assert "Nothing is ready to paste" in c.get("/ready").text
    after = _thread_state(settings.checkpointer_url, "d1")
    assert (
        after.config["configurable"]["checkpoint_id"]
        == before.config["configurable"]["checkpoint_id"]
    )
    assert after.next == ()


def test_mark_as_posted_needs_csrf(settings: InboxSettings, clock: Clock) -> None:
    seed_draft(settings.checkpointer_url)
    with _client(settings, clock) as c:
        login(c)
        _approve_draft(c)
        page = c.get("/ready").text
        action_id = re.search(r'action="/ready/([^/"]+)/posted"', page).group(1)  # type: ignore[union-attr]

        assert c.post(f"/ready/{action_id}/posted", data={"csrf": "wrong"}).status_code == 403
        assert action_id in c.get("/ready").text


def test_marking_an_unknown_draft_is_a_404(client: TestClient) -> None:
    login(client)
    page = client.get("/")
    response = client.post("/ready/nope/posted", data={"csrf": field(page.text, "csrf")})
    assert response.status_code == 404


def test_the_header_links_to_ready_to_paste(client: TestClient) -> None:
    login(client)
    assert 'href="/ready"' in client.get("/").text


# ---- runs saved by earlier versions; HTML errors only (C8, Hassan's phone report) --------


def seed_old_format(url: str, thread: str = "old1") -> None:
    """A run as an earlier version saved it: gate payload without `eval` (C4) or
    `failing` (early C7), no `revision` in the task, no `usage` in the state."""

    def c4_payload(action: Any) -> dict[str, Any]:
        return {
            "action_id": action.id,
            "task_id": action.task_id,
            "type": action.type,
            "target": action.target,
            "preview": action.preview,
            "risk_level": action.risk_level,
        }

    async def run() -> None:
        async with open_checkpointer(url) as saver:
            graph = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=saver)
            cfg: Any = {"configurable": {"thread_id": thread}, "recursion_limit": 40}
            state = {
                k: v
                for k, v in initial_ops_state(Task(agent="echo", goal="Old run.")).items()
                if k != "usage"
            }
            await graph.ainvoke(state, cfg)

    # Scoped: undoing the test's own monkeypatch would also undo the conftest's tmp_path
    # redirection of the judge log (the repo guard caught exactly that).
    from measured_ops import supervisor

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(supervisor, "gate_payload", c4_payload)
        asyncio.run(run())


def test_a_run_saved_by_an_earlier_version_opens_and_decides(
    settings: InboxSettings, clock: Clock
) -> None:
    seed_old_format(settings.checkpointer_url)
    with _client(settings, clock) as c:
        login(c)
        html = open_item(c, "old1")
        assert "Not evaluated" in html
        assert "First draft · up to 3 revisions" in html

        response = decide(c, html, "approve", thread="old1")

    assert response.status_code == 303
    assert _thread_state(settings.checkpointer_url, "old1").next == ()


def test_templates_are_loaded_once_at_startup(settings: InboxSettings, clock: Clock) -> None:
    # A running inbox must never pair its code with templates changed on disk later
    # (the phone's 500: old code in memory, new detail.html read from disk).
    app = create_inbox_app(settings, [EchoAgent()], load_policy(), clock=clock)
    env = app.state.templates
    assert env.auto_reload is False
    names = {
        "base.html",
        "detail.html",
        "list.html",
        "login.html",
        "message.html",
        "ready.html",
        "error.html",
    }
    assert names <= {key[1] for key in env.cache}


def _html_error(response: Any, status: int) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text.lower()
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers


def test_an_unknown_page_is_an_html_404(client: TestClient) -> None:
    _html_error(client.get("/no/such/page"), 404)


def test_a_wrong_method_is_an_html_405(client: TestClient) -> None:
    _html_error(client.put("/login"), 405)


def test_a_server_error_is_an_html_500_never_json(settings: InboxSettings, clock: Clock) -> None:
    seed(settings.checkpointer_url)
    app = create_inbox_app(settings, [EchoAgent()], load_policy(), clock=clock)
    with TestClient(
        app, base_url="https://testserver", follow_redirects=False, raise_server_exceptions=False
    ) as c:
        login(c)

        async def broken(thread_id: str) -> Any:
            raise RuntimeError("boom")

        app.state.inbox.get = broken
        response = c.get("/a/t1")

    _html_error(response, 500)
    assert "boom" not in response.text  # no internals on the page
    assert "Something went wrong" in response.text
