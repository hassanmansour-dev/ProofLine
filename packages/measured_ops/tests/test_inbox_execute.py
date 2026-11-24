"""The inbox runs approved actions (C5, ADR-0022): after the gate step it continues
through `execute`; a failure leaves the decision standing and the run stopped, and
Retry applies it once."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from tool_fakes import FakeJira, TriageStubAgent, fake_executors

from measured_ops import ApprovalPolicy, Task, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.inbox.service import Inbox
from measured_ops.supervisor import build_supervisor
from measured_ops.tools.errors import ToolError

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _setup(jira: FakeJira) -> tuple[Any, Inbox]:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    demo = load_policy()
    policy = ApprovalPolicy(version=1, actions={**demo.actions, "triage_ticket": "approval"})
    graph = build_supervisor(
        [TriageStubAgent(jira)], policy, executors=fake_executors(jira)
    ).compile(checkpointer=saver)
    return graph, Inbox(graph, policy, callbacks=[])


async def _start(graph: Any) -> None:
    cfg: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    await graph.ainvoke(initial_ops_state(Task(agent="ops", goal="Triage.")), cfg)


async def test_approving_a_move_applies_it_and_says_so() -> None:
    jira = FakeJira()
    graph, inbox = _setup(jira)
    await _start(graph)
    [item] = await inbox.list_pending()
    assert item.type == "triage_ticket"

    outcome = await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
    )

    assert [o.status for o in outcome.executed] == ["applied"]
    assert not outcome.failed
    assert jira.issues["DEMO-1"]["status"] == "In Progress"
    assert await inbox.list_pending() == []
    assert await inbox.in_progress() == []


async def test_a_failed_execution_keeps_the_decision_and_stops_the_run() -> None:
    jira = FakeJira()
    graph, inbox = _setup(jira)
    await _start(graph)
    [item] = await inbox.list_pending()
    jira.fail_next, jira.fail_only, jira.error = 1, {"get_issue"}, ToolError("fake: refused")

    outcome = await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
    )

    assert outcome.failed
    assert outcome.approved
    [status] = await inbox.in_progress()
    assert status.state == "stopped"
    snap = await graph.aget_state({"configurable": {"thread_id": "t"}})
    assert [d.approved for d in snap.values["decisions"]] == [True]  # the decision stands

    await inbox.retry("t")
    await inbox.wait_idle()

    snap = await graph.aget_state({"configurable": {"thread_id": "t"}})
    assert [o.status for o in snap.values["executed"]] == ["applied"]
    assert len([w for w in jira.writes if w[0] == "transition"]) == 1
    assert await inbox.in_progress() == []


async def test_after_a_restart_an_approved_unrun_action_is_stopped_at_once() -> None:
    # Only the inbox gets past an approval, so a run waiting to execute an approved action
    # is stopped (Retry, or the startup sweep), not "elsewhere" for twice the timeout.
    jira = FakeJira()
    graph, _ = _setup(jira)
    await _start(graph)
    first = Inbox(graph, load_policy(), callbacks=[])
    [item] = await first.list_pending()
    jira.fail_next, jira.fail_only, jira.error = 1, {"get_issue"}, ToolError("fake: refused")
    await first.decide("t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True)

    restarted = Inbox(graph, load_policy(), callbacks=[])  # new process: no memory of it
    [status] = await restarted.in_progress()

    assert status.state == "stopped"


async def test_approving_the_last_action_of_a_revised_round_runs_it_then_revises() -> None:
    # The new continuation: execute runs in the request, the revision in the background.
    from ops_fakes import ScriptedAgent, make_task

    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    agent = ScriptedAgent([("publish_public", "medium", "A."), ("publish_public", "medium", "B.")])
    graph = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
    cfg: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    await graph.ainvoke(initial_ops_state(make_task()), cfg)
    inbox = Inbox(graph, load_policy(), callbacks=[])
    [first] = await inbox.list_pending()
    await inbox.decide(
        "t",
        interrupt_id=first.interrupt_id,
        action_id=first.action_id,
        approved=False,
        feedback="Shorter.",
    )
    [second] = await inbox.list_pending()

    outcome = await inbox.decide(
        "t", interrupt_id=second.interrupt_id, action_id=second.action_id, approved=True
    )
    await inbox.wait_idle()

    assert outcome.revising
    assert [(o.action_id, o.status) for o in outcome.executed] == [(second.action_id, "manual")]
    assert agent.runs == 2  # the revision ran (in the background)
    [again] = await inbox.list_pending()
    assert again.revision == 1


async def test_kill_switch_stops_an_approved_write_until_it_is_released() -> None:
    # Review B1: the switch must stop writes, not only agent runs.
    from measured_ops.killswitch import KillSwitch, KillSwitchEngaged

    jira = FakeJira()
    graph, inbox = _setup(jira)
    await _start(graph)
    [item] = await inbox.list_pending()
    switch = KillSwitch.from_env()
    switch.engage("testing")

    outcome = await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
    )

    assert outcome.failed
    assert outcome.kill_switch
    assert jira.writes == []
    assert jira.issues["DEMO-1"]["status"] == "To Do"
    snap = await graph.aget_state({"configurable": {"thread_id": "t"}})
    assert snap.next == ("execute",)  # stopped there, the decision saved
    assert [d.approved for d in snap.values["decisions"]] == [True]
    with pytest.raises(KillSwitchEngaged):
        await inbox.retry("t")
    assert jira.writes == []

    switch.release()
    await inbox.retry("t")
    await inbox.wait_idle()

    snap = await graph.aget_state({"configurable": {"thread_id": "t"}})
    assert [o.status for o in snap.values["executed"]] == ["applied"]
    assert len([w for w in jira.writes if w[0] == "transition"]) == 1


async def test_kill_switch_does_not_block_a_manual_outcome() -> None:
    # Nothing runs for a type without an executor, so recording it is not stopped.
    from ops_fakes import ScriptedAgent, make_task

    from measured_ops.killswitch import KillSwitch

    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    agent = ScriptedAgent([("approve_draft", "medium", "## LinkedIn post\nText.")])
    graph = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
    cfg: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    await graph.ainvoke(initial_ops_state(make_task()), cfg)
    inbox = Inbox(graph, load_policy(), callbacks=[])
    [item] = await inbox.list_pending()
    KillSwitch.from_env().engage("testing")

    outcome = await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
    )

    assert not outcome.failed
    assert [o.status for o in outcome.executed] == ["manual"]


async def test_a_hanging_execution_is_cut_off_and_the_run_is_stopped() -> None:
    # Review: the continuation after a decision had no time bound, so a hanging tool
    # held the phone's request (and the thread's lock) forever.
    import asyncio

    jira = FakeJira()
    graph, _ = _setup(jira)
    await _start(graph)
    inbox = Inbox(graph, load_policy(), callbacks=[], execute_timeout_s=0.05)
    [item] = await inbox.list_pending()
    real_get = jira.get_issue
    hang = True

    async def slow_get(key: str) -> Any:
        if hang:
            await asyncio.sleep(30)
        return await real_get(key)

    jira.get_issue = slow_get  # type: ignore[method-assign]

    outcome = await asyncio.wait_for(
        inbox.decide("t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True),
        timeout=5,
    )

    assert outcome.failed
    assert outcome.timed_out
    [status] = await inbox.in_progress()
    assert status.state == "stopped"
    assert jira.writes == []

    hang = False
    await inbox.retry("t")
    await inbox.wait_idle()
    assert jira.issues["DEMO-1"]["status"] == "In Progress"
    assert len([w for w in jira.writes if w[0] == "transition"]) == 1
