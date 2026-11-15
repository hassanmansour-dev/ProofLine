"""Background revisions in the inbox (C8, ADR-0019).

A rejection with feedback returns at once; the revision runs in the background, one
at a time, with a timeout. A run cut off (timeout, error, restart) shows as stopped and
can be retried; on start the inbox continues each stopped run once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from ops_fakes import GatedAgent, make_task

from measured_ops import initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.inbox.service import AlreadyDecided, Inbox, Refused
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _graph(saver: InMemorySaver, agent: Any) -> Any:
    return build_supervisor([agent], load_policy()).compile(checkpointer=saver)


def _saver() -> InMemorySaver:
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))


async def _paused(graph: Any, thread: str) -> None:
    cfg = {"configurable": {"thread_id": thread}, "recursion_limit": 40}
    await graph.ainvoke(initial_ops_state(make_task()), cfg)


async def _reject(inbox: Inbox, thread: str, feedback: str | None = "Shorter.") -> Any:
    item = next(i for i in await inbox.list_pending() if i.thread_id == thread)
    return await inbox.decide(
        thread,
        interrupt_id=item.interrupt_id,
        action_id=item.action_id,
        approved=False,
        feedback=feedback,
    )


async def test_reject_with_feedback_returns_at_once_and_revises_in_the_background() -> None:
    agent = GatedAgent()
    graph = _graph(_saver(), agent)
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])

    outcome = await _reject(inbox, "t")

    assert outcome.revising is True
    assert await inbox.list_pending() == []  # not waiting while it revises
    await asyncio.sleep(0)
    [status] = await inbox.in_progress()
    assert (status.thread_id, status.state) == ("t", "running")

    agent.release.set()
    await inbox.wait_idle()

    [again] = await inbox.list_pending()
    assert again.preview == "Draft 1."
    assert await inbox.in_progress() == []


async def test_a_second_tap_while_revising_is_already_decided() -> None:
    agent = GatedAgent()
    graph = _graph(_saver(), agent)
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])
    item = (await inbox.list_pending())[0]
    await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=False, feedback="x"
    )

    with pytest.raises(AlreadyDecided):
        await inbox.decide(
            "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
        )

    agent.release.set()
    await inbox.wait_idle()
    assert agent.runs == 2


async def test_revisions_run_one_at_a_time() -> None:
    agent = GatedAgent()
    saver = _saver()
    graph = _graph(saver, agent)
    for thread in ("a", "b"):
        await _paused(graph, thread)
    inbox = Inbox(graph, load_policy(), callbacks=[])

    await _reject(inbox, "a")
    await _reject(inbox, "b")
    await asyncio.sleep(0.01)

    states = {s.thread_id: s.state for s in await inbox.in_progress()}
    assert sorted(states.values()) == ["queued", "running"]
    agent.release.set()
    await inbox.wait_idle()
    assert agent.max_active == 1
    assert {i.thread_id for i in await inbox.list_pending()} == {"a", "b"}


async def test_a_revision_that_times_out_is_stopped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = GatedAgent()  # never released
    graph = _graph(_saver(), agent)
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=0.05)

    with caplog.at_level(logging.WARNING):
        await _reject(inbox, "t")
        await inbox.wait_idle()

    assert "timed out" in caplog.text
    assert "agent 'echo' failed after using" in caplog.text  # the cut-off pass's usage
    [status] = await inbox.in_progress()
    assert (status.thread_id, status.state) == ("t", "stopped")


async def test_retry_continues_a_stopped_revision() -> None:
    agent = GatedAgent()
    graph = _graph(_saver(), agent)
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=0.05)
    await _reject(inbox, "t")
    await inbox.wait_idle()

    inbox.revision_timeout_s = 5
    agent.release.set()
    await inbox.retry("t")
    await inbox.wait_idle()

    [again] = await inbox.list_pending()
    assert again.preview == "Draft 1."


async def test_retry_refuses_a_run_that_is_not_stopped() -> None:
    graph = _graph(_saver(), GatedAgent())
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])

    with pytest.raises(AlreadyDecided):
        await inbox.retry("t")  # it's waiting at the gate, not stopped


async def test_on_start_the_inbox_continues_each_stopped_run_once() -> None:
    saver = _saver()
    broken = GatedAgent(fail_revisions=True)
    graph = _graph(saver, broken)
    await _paused(graph, "t")
    first = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=0.01)
    await _reject(first, "t")
    await first.wait_idle()  # the revision raised: the run is stopped
    await asyncio.sleep(0.02)  # older than the timeout: not a run in progress elsewhere

    fixed = GatedAgent()
    fixed.release.set()
    restarted = Inbox(_graph(saver, fixed), load_policy(), callbacks=[], revision_timeout_s=0.01)
    await restarted.start()
    restarted.revision_timeout_s = 30  # stale by the tiny timeout; the revision itself gets time
    await restarted.wait_idle()
    await restarted.start()  # a second call does nothing

    [again] = await restarted.list_pending()
    assert again.preview == "Draft 1."
    assert fixed.runs == 1


async def test_a_fresh_run_between_nodes_is_in_progress_elsewhere_not_stopped() -> None:
    # A `proofline propose` run in another process looks the same in the checkpoint;
    # only one older than the timeout (or cut off here) counts as stopped.
    from langgraph.types import Command

    from measured_ops import ApprovalDecision

    saver = _saver()
    graph = _graph(saver, GatedAgent(fail_revisions=True))
    await _paused(graph, "t")
    cfg: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    action_id = (await graph.aget_state(cfg)).values["pending_actions"][0].id
    decision = ApprovalDecision(action_id=action_id, approved=False, feedback="x")
    with pytest.raises(RuntimeError, match="revision broke"):  # cut off outside the inbox
        await graph.ainvoke(Command(resume=decision.model_dump()), cfg)
    inbox = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=600)

    [status] = await inbox.in_progress()
    assert status.state == "elsewhere"
    await inbox.start()
    await inbox.wait_idle()
    assert (await inbox.in_progress())[0].state == "elsewhere"  # not continued


async def test_reject_with_feedback_for_another_action_is_refused_at_once() -> None:
    graph = _graph(_saver(), GatedAgent())
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])
    item = (await inbox.list_pending())[0]

    with pytest.raises(Refused, match="not the action waiting"):
        await inbox.decide(
            "t", interrupt_id=item.interrupt_id, action_id="other", approved=False, feedback="x"
        )
    assert [i.thread_id for i in await inbox.list_pending()] == ["t"]


async def test_approve_and_reject_without_feedback_stay_synchronous() -> None:
    graph = _graph(_saver(), GatedAgent())
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])

    outcome = await _reject(inbox, "t", feedback=None)

    assert outcome.revising is False
    assert await inbox.list_pending() == [] == await inbox.in_progress()


async def test_close_cancels_running_revisions() -> None:
    agent = GatedAgent()
    graph = _graph(_saver(), agent)
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])
    await _reject(inbox, "t")
    await asyncio.sleep(0)

    await inbox.aclose()

    assert agent.active == 0
