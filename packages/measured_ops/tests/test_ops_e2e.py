"""End-to-end supervisor cases (C2): task in → echo agent → approval interrupt →
process restart → resume → done, against SQLite and Postgres. Plus a refused
answer after the restart, which must leave the run paused.

The first half runs in a separate Python process (ops_e2e_child.py), so the resume
can only come from the checkpoint. Strict msgpack is on in both processes.

Postgres: set MEASURED_OPS_TEST_DATABASE_URL (CI sets it for its service container).
The `checkpointer_url` fixture (conftest.py) skips the Postgres case locally without
it and fails it in CI, so CI can never skip it quietly.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from e2e_support import pause_in_another_process
from langgraph.types import Command
from pydantic import ValidationError

from measured_ops import ApprovalDecision, load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.echo import EchoAgent
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio


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

    paused = pause_in_another_process(checkpointer_url, thread_id)
    decision = ApprovalDecision(action_id=paused["action_id"], approved=True)
    done = await _resume_here(checkpointer_url, thread_id, decision)

    assert paused["type"] == "publish_public"
    assert "Say why every AI loop needs an exit." in paused["preview"]
    assert [d.approved for d in done["decisions"]] == [True]
    assert [a.id for a in done["ready"]] == [paused["action_id"]]
    assert done["pending_actions"] == []
    assert [r.status for r in done["results"]] == ["succeeded"]


async def test_task_to_rejection_to_revision_survives_restart(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-reject-{uuid4().hex}"

    paused = pause_in_another_process(checkpointer_url, thread_id)
    decision = ApprovalDecision(action_id=paused["action_id"], approved=False, feedback="Shorter.")
    done = await _resume_here(checkpointer_url, thread_id, decision)

    [recorded] = done["decisions"]
    assert (recorded.approved, recorded.feedback) == (False, "Shorter.")
    assert done["ready"] == []
    assert done["blocked"] == []
    # ADR-0019: feedback revises in the same thread, after the restart too
    [again] = done["__interrupt__"]
    assert again.value["action_id"] != paused["action_id"]
    assert done["task"].inputs["revision"] == 1
    assert done["task"].inputs["previous_preview"] == paused["preview"]


async def test_refused_answer_after_restart_keeps_the_run_paused(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate refuses bad answers inside interrupt(), so nothing is bound to the
    # gate task (ADR-0016). Pinned on each saver, since each stores resume writes.
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-refuse-{uuid4().hex}"
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    paused = pause_in_another_process(checkpointer_url, thread_id)
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


# ---- the eval node across a restart (C7, ADR-0018) ----------------------------------------


async def test_failed_check_pauses_an_auto_action_and_survives_restart(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"e2e-eval-{uuid4().hex}"
    goal = "Reply to jane.doe@example.com about loops."

    paused = pause_in_another_process(checkpointer_url, thread_id, "draft_content", goal)
    decision = ApprovalDecision(action_id=paused["action_id"], approved=True)
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    async with open_checkpointer(checkpointer_url) as saver:
        app = build_supervisor([EchoAgent("draft_content")], load_policy()).compile(
            checkpointer=saver
        )
        done: dict[str, Any] = await app.ainvoke(Command(resume=decision.model_dump()), cfg)

    assert paused["type"] == "draft_content"  # an "auto" row, raised to approval
    assert paused["eval"]["failing"] == ["pii"]
    assert paused["eval"]["reasons"] == {"pii": "email found"}
    [ready] = done["ready"]
    assert ready.eval_scores is not None
    assert ready.eval_scores.failing == ("pii",)


async def test_clean_auto_action_runs_without_pausing(
    checkpointer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    from measured_ops import Task, initial_ops_state

    cfg: Any = {"configurable": {"thread_id": f"e2e-auto-{uuid4().hex}"}, "recursion_limit": 40}
    async with open_checkpointer(checkpointer_url) as saver:
        app = build_supervisor([EchoAgent("draft_content")], load_policy()).compile(
            checkpointer=saver
        )
        out: dict[str, Any] = await app.ainvoke(
            initial_ops_state(Task(agent="echo", goal="Say why loops need exits.")), cfg
        )

    assert "__interrupt__" not in out
    [ready] = out["ready"]
    assert ready.eval_scores is not None
    assert ready.eval_scores.passed
