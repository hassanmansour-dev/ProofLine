"""End-to-end supervisor cases. C2 builds the supervisor and makes these pass.

Moved here from proofline's test_graph.py (ADR-0014): approval and resume-after-restart
belong to the supervisor's gate, not to the content pipeline. C2 swaps SQLite for
the Postgres checkpointer where CI has one; the strict serde check stays.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from ops_fakes import EchoAgent, make_task

from measured_ops import ApprovalDecision, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST

pytestmark = pytest.mark.anyio


def _saver(conn: aiosqlite.Connection) -> AsyncSqliteSaver:
    return AsyncSqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))


@pytest.mark.xfail(strict=True, raises=ImportError, reason="C2 target")
async def test_task_to_approval_to_done_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from measured_ops.supervisor import build_supervisor  # type: ignore[import-not-found]

    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    task = make_task()
    cfg: Any = {"configurable": {"thread_id": "e2e"}, "recursion_limit": 40}
    db = tmp_path / "cp.db"

    async with aiosqlite.connect(db) as conn:
        app = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=_saver(conn))
        first = await app.ainvoke(initial_ops_state(task), cfg)
        [intr] = first["__interrupt__"]  # publish_public → approval gate
        action_id = intr.value["action_id"]

    async with aiosqlite.connect(db) as conn:  # "new process"
        app = build_supervisor([EchoAgent()], load_policy()).compile(checkpointer=_saver(conn))
        decision = ApprovalDecision(action_id=action_id, approved=True)
        done = await app.ainvoke(Command(resume=decision.model_dump()), cfg)

    assert [d.approved for d in done["decisions"]] == [True]
    assert done["pending_actions"] == []
