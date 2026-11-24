"""A run that ended as failed (e.g. stopped by its budget) is shown, not silent (review 2)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from ops_fakes import LLMAgent, make_task

from measured_ops import initial_ops_state, load_policy
from measured_ops.budget import Budget
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.digest import build_digest
from measured_ops.inbox.service import Inbox
from measured_ops.killswitch import KillSwitch
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio
CFG: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}


def _reply() -> AIMessage:
    return AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        response_metadata={"model_name": "model-a"},
    )


async def _revision_over_budget(caplog: pytest.LogCaptureFixture) -> Inbox:
    """First pass: one call, within a cap of two. The revision needs two more: over."""
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    agent = LLMAgent([_reply()] * 3, action_type="publish_public")
    agent.calls = 1
    graph = build_supervisor([agent], load_policy(), budget=Budget(max_calls=2), prices={}).compile(
        checkpointer=saver
    )
    await graph.ainvoke(initial_ops_state(make_task(goal="Explain loop exits.")), CFG)
    inbox = Inbox(graph, load_policy(), callbacks=[])
    [item] = await inbox.list_pending()
    agent.calls = 2
    with caplog.at_level(logging.INFO, logger="measured_ops.inbox"):
        outcome = await inbox.decide(
            "t",
            interrupt_id=item.interrupt_id,
            action_id=item.action_id,
            approved=False,
            feedback="Shorter.",
        )
        await inbox.wait_idle()
    assert outcome.revising
    return inbox


async def test_a_revision_stopped_by_its_budget_is_listed_as_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inbox = await _revision_over_budget(caplog)

    assert await inbox.list_pending() == []  # no new draft is coming
    [failed] = await inbox.failed_runs()
    assert (failed.thread_id, failed.agent, failed.goal) == ("t", "echo", "Explain loop exits.")
    assert failed.error == "budget: the next call would cross max_calls: 3 > 2"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("run t ended as failed" in m and "max_calls" in m for m in errors)
    assert not any("revised" in r.getMessage() for r in caplog.records)


async def test_old_failures_drop_off_the_list(caplog: pytest.LogCaptureFixture) -> None:
    inbox = await _revision_over_budget(caplog)
    later = datetime.now(UTC) + timedelta(days=8)
    assert await inbox.failed_runs(now=later) == []


async def test_the_digest_has_a_line_for_it(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    inbox = await _revision_over_budget(caplog)
    digest = await build_digest(inbox, prices={}, kill_switch=KillSwitch(tmp_path / "k"))
    assert "## Failed (1): ended, nothing more is coming" in digest.markdown
    assert "- Explain loop exits. · echo · budget: the next call would cross max_calls: 3 > 2" in (
        digest.markdown
    )
