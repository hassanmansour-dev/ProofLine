"""Inbox end to end (C4 done-when): task in → echo agent → pause at the gate in another
process → sign in to the inbox over HTTP → approve or reject → the thread resumes from
the checkpoint and finishes. SQLite and Postgres (see conftest.checkpointer_url).

Edit is deferred to C8 (ADR-0017), so the paths are approve and reject.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from e2e_support import pause_in_another_process
from fastapi.testclient import TestClient

from measured_ops import load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.echo import EchoAgent
from measured_ops.inbox.auth import hash_passphrase
from measured_ops.inbox.settings import InboxSettings
from measured_ops.inbox.web import create_inbox_app
from measured_ops.supervisor import build_supervisor

PASSPHRASE = "e2e passphrase, demo only"


def _field(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, name
    return match.group(1)


def _final_state(url: str, thread_id: str) -> Any:
    async def read() -> Any:
        async with open_checkpointer(url) as saver:
            graph = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=saver)
            return await graph.aget_state({"configurable": {"thread_id": thread_id}})

    return asyncio.run(read())


@pytest.mark.parametrize(("decision", "feedback"), [("approve", ""), ("reject", "Shorter.")])
def test_decide_in_the_inbox_resumes_the_paused_thread(
    checkpointer_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    feedback: str,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-inbox-{decision}-{uuid4().hex}"
    paused = pause_in_another_process(checkpointer_url, thread_id)
    settings = InboxSettings(
        passphrase_hash=hash_passphrase(PASSPHRASE, n=2**10),
        session_secret="e" * 40,
        checkpointer_url=checkpointer_url,
        lock_path=tmp_path / "inbox.lock",
    )
    app = create_inbox_app(settings, [EchoAgent()], load_policy())

    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        login_page = c.get("/login")
        c.post("/login", data={"passphrase": PASSPHRASE, "csrf": _field(login_page.text, "csrf")})
        assert thread_id in c.get("/").text
        html = c.get(f"/a/{thread_id}").text
        assert "Say why every AI loop needs an exit." in html
        assert _field(html, "action_id") == paused["action_id"]

        response = c.post(
            f"/a/{thread_id}/decision",
            data={
                "csrf": _field(html, "csrf"),
                "interrupt_id": _field(html, "interrupt_id"),
                "action_id": _field(html, "action_id"),
                "decision": decision,
                "feedback": feedback,
            },
        )
        assert response.status_code == 303
        assert thread_id not in c.get("/").text

    snap = _final_state(checkpointer_url, thread_id)
    assert snap.next == ()
    [recorded] = snap.values["decisions"]
    assert recorded.action_id == paused["action_id"]
    assert recorded.approved is (decision == "approve")
    assert recorded.feedback == (feedback or None)
    ready = [a.id for a in snap.values["ready"]]
    assert ready == ([paused["action_id"]] if decision == "approve" else [])
