"""C5 end to end (GROW-84's done-when): an agent reads the tracker, proposes a ticket move,
and approval applies it, across a process restart, over the real MCP client path (stdio,
the public demo config and demo servers), on SQLite and Postgres.

The first half runs in another process (tools_e2e_child.py), so the resume can only come
from the checkpoint; the tracker's state is a file both processes reach through the demo
MCP server. Strict msgpack is on in both.
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
from tools_e2e_child import supervisor

from measured_ops import ApprovalDecision
from measured_ops.checkpoint import open_checkpointer
from measured_ops.tools.demo_servers import STATE_ENV, DemoState

pytestmark = pytest.mark.anyio
CHILD = Path(__file__).with_name("tools_e2e_child.py")


@pytest.fixture
def tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DemoState:
    path = tmp_path / "demo_tools.json"
    monkeypatch.setenv(STATE_ENV, str(path))
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    return DemoState(path)


def _child(url: str, thread_id: str, mode: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CHILD), url, thread_id, mode],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    out: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


async def _resume(url: str, thread_id: str, decision: ApprovalDecision) -> dict[str, Any]:
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    async with open_checkpointer(url) as saver:
        app = supervisor("approval").compile(checkpointer=saver)
        assert (await app.aget_state(cfg)).next == ("gate",)  # survived the restart
        done: dict[str, Any] = await app.ainvoke(Command(resume=decision.model_dump()), cfg)
    return done


async def test_read_propose_approve_apply_across_a_restart(
    checkpointer_url: str, tracker: DemoState
) -> None:
    thread_id = f"tools-approve-{uuid4().hex}"

    paused = _child(checkpointer_url, thread_id, "approval")["paused"]

    assert paused["type"] == "triage_ticket"
    assert paused["target"] == "jira:DEMO-1"
    assert paused["preview"].startswith("Ticket triage: DEMO-1\n- Move to: In Progress")
    assert tracker.load()["jira"]["issues"]["DEMO-1"]["status"] == "To Do"  # not yet

    decision = ApprovalDecision(action_id=paused["action_id"], approved=True)
    done = await _resume(checkpointer_url, thread_id, decision)

    [outcome] = done["executed"]
    assert (outcome.action_id, outcome.status) == (paused["action_id"], "applied")
    assert outcome.detail == "DEMO-1: moved to In Progress"
    assert tracker.load()["jira"]["issues"]["DEMO-1"]["status"] == "In Progress"


async def test_rejected_move_is_never_applied_across_a_restart(
    checkpointer_url: str, tracker: DemoState
) -> None:
    thread_id = f"tools-reject-{uuid4().hex}"
    paused = _child(checkpointer_url, thread_id, "approval")["paused"]

    done = await _resume(
        checkpointer_url, thread_id, ApprovalDecision(action_id=paused["action_id"], approved=False)
    )

    assert done["executed"] == []
    assert tracker.load()["jira"]["issues"]["DEMO-1"]["status"] == "To Do"


async def test_auto_triage_applies_without_pausing(
    checkpointer_url: str, tracker: DemoState
) -> None:
    thread_id = f"tools-auto-{uuid4().hex}"

    [outcome] = _child(checkpointer_url, thread_id, "auto")["executed"]

    assert (outcome["type"], outcome["status"]) == ("triage_ticket", "applied")
    assert tracker.load()["jira"]["issues"]["DEMO-1"]["status"] == "In Progress"
    cfg: Any = {"configurable": {"thread_id": thread_id}}
    async with open_checkpointer(checkpointer_url) as saver:
        app = supervisor("auto").compile(checkpointer=saver)
        snap = await app.aget_state(cfg)
    assert snap.next == ()
    assert [o.status for o in snap.values["executed"]] == ["applied"]
