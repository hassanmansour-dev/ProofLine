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


async def test_a_fresh_first_pass_between_nodes_is_elsewhere_not_stopped() -> None:
    # A `proofline propose` run in another process looks like this in the checkpoint;
    # a first pass (no parent task) only counts as stopped once it's stale.
    saver = _saver()
    graph = _graph(saver, GatedAgent(fail_first=True))
    with pytest.raises(RuntimeError, match="first pass broke"):  # cut off outside the inbox
        await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=600)

    [status] = await inbox.in_progress()
    assert status.state == "elsewhere"
    await inbox.start()
    await inbox.wait_idle()
    assert (await inbox.in_progress())[0].state == "elsewhere"  # not continued


async def test_a_queued_revision_survives_a_restart_with_its_feedback() -> None:
    # Review finding: the decision is saved before the phone gets its answer, so a
    # revision still queued at shutdown isn't lost.
    saver = _saver()
    agent = GatedAgent()
    graph = _graph(saver, agent)
    for thread in ("a", "b"):
        await _paused(graph, thread)
    inbox = Inbox(graph, load_policy(), callbacks=[])
    await _reject(inbox, "a")
    await _reject(inbox, "b", feedback="B feedback.")
    await asyncio.sleep(0.01)
    assert sorted(s.state for s in await inbox.in_progress()) == ["queued", "running"]
    await inbox.aclose()

    fresh = GatedAgent()
    fresh.release.set()
    restarted = Inbox(_graph(saver, fresh), load_policy(), callbacks=[], revision_timeout_s=600)
    assert sorted(s.state for s in await restarted.in_progress()) == ["stopped", "stopped"]
    await restarted.start()  # revisions are stopped at once, whatever their age
    await restarted.wait_idle()

    cfg: Any = {"configurable": {"thread_id": "b"}, "recursion_limit": 40}
    values = (await restarted.graph.aget_state(cfg)).values
    assert [d.feedback for d in values["decisions"]] == ["B feedback."]
    assert values["task"].inputs["revision"] == 1
    assert {i.thread_id for i in await restarted.list_pending()} == {"a", "b"}


async def test_approving_the_last_action_of_a_rejected_round_revises_in_the_background() -> None:
    # Review finding: the second decision of a round must not run the agent in the request.
    agent = GatedAgent(actions=2)
    graph = _graph(_saver(), agent)
    await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[])

    first = await _reject(inbox, "t", feedback="A: shorter.")
    assert (first.revising, first.still_paused) == (False, True)  # B still waits
    [b] = await inbox.list_pending()
    second = await inbox.decide(
        "t", interrupt_id=b.interrupt_id, action_id=b.action_id, approved=True
    )

    assert second.revising is True
    assert agent.runs == 1  # the revision hasn't run inside the call
    agent.release.set()
    await inbox.wait_idle()
    cfg: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    values = (await graph.aget_state(cfg)).values
    assert [a.preview for a in values["ready"]] == ["Draft 0b."]  # an approval stands
    assert values["task"].inputs["revision"] == 1


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


@pytest.mark.parametrize(("age_factor", "state"), [(1.5, "elsewhere"), (2.5, "stopped")])
async def test_a_first_pass_is_stale_only_after_twice_the_timeout(
    monkeypatch: pytest.MonkeyPatch, age_factor: float, state: str
) -> None:
    # `proofline propose` runs under the same timeout, so a run still going elsewhere is
    # always cut off well before the inbox would offer to retry it.
    from measured_ops.inbox import service

    saver = _saver()
    graph = _graph(saver, GatedAgent(fail_first=True))
    with pytest.raises(RuntimeError):
        await _paused(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=100)
    monkeypatch.setattr(service, "_age_s", lambda created_at: 100 * age_factor)

    [status] = await inbox.in_progress()
    assert status.state == state


def test_revision_timeout_from_env_needs_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from measured_ops.inbox.settings import revision_timeout_from_env

    monkeypatch.delenv("MEASURED_OPS_INBOX_REVISION_TIMEOUT_S", raising=False)
    assert revision_timeout_from_env() == 600.0
    monkeypatch.setenv("MEASURED_OPS_INBOX_REVISION_TIMEOUT_S", "42")
    assert revision_timeout_from_env() == 42.0
