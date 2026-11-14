"""End-to-end supervisor cases (C2): task in → echo agent → approval interrupt →
process restart → resume → done, against SQLite and Postgres. Plus a refused
answer after the restart, which must leave the run paused.

The first half runs in a separate Python process (ops_e2e_child.py), so the resume
can only come from the checkpoint. Strict msgpack is on in both processes.

Postgres: set MEASURED_OPS_TEST_DATABASE_URL (CI sets it for its service container;
locally, point it at proofline-pg on 127.0.0.1:55432). Without it the Postgres case
is skipped locally and fails in CI, so CI can never skip it quietly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from langgraph.types import Command
from pydantic import ValidationError

from measured_ops import ApprovalDecision, load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.echo import EchoAgent
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio

CHILD = Path(__file__).with_name("ops_e2e_child.py")
PG_ENV = "MEASURED_OPS_TEST_DATABASE_URL"


@pytest.fixture(params=["sqlite", "postgres"])
def checkpointer_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'cp.db'}"
    url = os.environ.get(PG_ENV)
    if not url:
        if os.environ.get("CI"):
            pytest.fail(f"{PG_ENV} must be set in CI: the Postgres e2e case can't be skipped")
        pytest.skip(f"set {PG_ENV} to run the Postgres case")
    return url


def _pause_in_another_process(url: str, thread_id: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CHILD), url, thread_id],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return payload


async def _resume_here(url: str, thread_id: str, decision: ApprovalDecision) -> dict[str, Any]:
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    async with open_checkpointer(url) as saver:
        app = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=saver)
        snap = await app.aget_state(cfg)
        assert snap.next == ("gate",)  # the pause survived the process exit
        done: dict[str, Any] = await app.ainvoke(Command(resume=decision.model_dump()), cfg)
    return done


async def test_task_to_approval_to_done_survives_restart(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-approve-{uuid4().hex}"

    paused = _pause_in_another_process(checkpointer_url, thread_id)
    decision = ApprovalDecision(action_id=paused["action_id"], approved=True)
    done = await _resume_here(checkpointer_url, thread_id, decision)

    assert paused["type"] == "publish_public"
    assert "Say why every AI loop needs an exit." in paused["preview"]
    assert [d.approved for d in done["decisions"]] == [True]
    assert [a.id for a in done["ready"]] == [paused["action_id"]]
    assert done["pending_actions"] == []
    assert [r.status for r in done["results"]] == ["succeeded"]


async def test_task_to_rejection_to_done_survives_restart(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-reject-{uuid4().hex}"

    paused = _pause_in_another_process(checkpointer_url, thread_id)
    decision = ApprovalDecision(action_id=paused["action_id"], approved=False, feedback="Shorter.")
    done = await _resume_here(checkpointer_url, thread_id, decision)

    [recorded] = done["decisions"]
    assert (recorded.approved, recorded.feedback) == (False, "Shorter.")
    assert done["ready"] == []
    assert done["blocked"] == []
    assert done["pending_actions"] == []


async def test_refused_answer_after_restart_keeps_the_run_paused(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate refuses bad answers inside interrupt(), so nothing is bound to the
    # gate task (ADR-0016). Pinned on each saver, since each stores resume writes.
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-refuse-{uuid4().hex}"
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    paused = _pause_in_another_process(checkpointer_url, thread_id)
    wrong = ApprovalDecision(action_id="not-the-waiting-action", approved=True)
    async with open_checkpointer(checkpointer_url) as saver:
        app = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=saver)
        with pytest.raises(ValidationError, match="not the action waiting"):
            await app.ainvoke(Command(resume=wrong.model_dump()), cfg)
        snap = await app.aget_state(cfg)
        assert snap.next == ("gate",)
        assert [i.value["action_id"] for i in snap.interrupts] == [paused["action_id"]]

    right = ApprovalDecision(action_id=paused["action_id"], approved=True)
    done = await _resume_here(checkpointer_url, thread_id, right)

    assert [a.id for a in done["ready"]] == [paused["action_id"]]
    assert [d.action_id for d in done["decisions"]] == [paused["action_id"]]
