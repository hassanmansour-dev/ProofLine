"""End-to-end supervisor cases. C2 builds the supervisor and makes these pass.

Moved here from proofline's test_graph.py (ADR-0014): approval and resume-after-restart
belong to the supervisor's gate, not to the content pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ops_fakes import EchoAgent, make_task

pytestmark = pytest.mark.anyio


@pytest.mark.xfail(strict=True, reason="C2 target")
async def test_task_to_approval_to_done_survives_restart(tmp_path: Path) -> None:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command

    from measured_ops import ApprovalDecision, initial_ops_state, load_policy
    from measured_ops.supervisor import build_supervisor  # type: ignore[import-not-found]

    task = make_task()
    cfg: Any = {"configurable": {"thread_id": "e2e"}}
    db = tmp_path / "cp.db"

    async with aiosqlite.connect(db) as conn:
        app = build_supervisor([EchoAgent()], load_policy()).compile(
            checkpointer=AsyncSqliteSaver(conn)
        )
        first = await app.ainvoke(initial_ops_state(task), cfg)
        [intr] = first["__interrupt__"]  # publish_public → approval gate
        action_id = intr.value["action_id"]

    async with aiosqlite.connect(db) as conn:  # "new process"
        app = build_supervisor([EchoAgent()], load_policy()).compile(
            checkpointer=AsyncSqliteSaver(conn)
        )
        decision = ApprovalDecision(action_id=action_id, approved=True)
        done = await app.ainvoke(Command(resume=decision.model_dump()), cfg)

    assert [d.approved for d in done["decisions"]] == [True]
    assert done["pending_actions"] == []
