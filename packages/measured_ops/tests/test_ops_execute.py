"""The supervisor's execute node (C5, ADR-0022): ready actions run through the executors,
one per step; failures stop the run loudly; nothing runs twice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from langgraph.graph import START as GRAPH_START
from langgraph.types import Command
from ops_fakes import ScriptedAgent, make_task
from tool_fakes import FakeCalendar, FakeJira, TriageStubAgent, fake_executors

from measured_ops import (
    AgentContractError,
    AgentInput,
    AgentOutput,
    AgentResult,
    ApprovalDecision,
    initial_ops_state,
    load_policy,
)
from measured_ops.contracts import SERDE_ALLOWLIST, ActionOutcome, Task
from measured_ops.policy import ApprovalPolicy
from measured_ops.supervisor import build_supervisor, route_after_gate
from measured_ops.tools.errors import ToolError, ToolUnavailableError
from measured_ops.tools.executors import Executors


class _S(TypedDict, total=False):
    task: Task
    result: AgentResult


pytestmark = pytest.mark.anyio
CFG: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
START = datetime(2026, 11, 24, 14, tzinfo=UTC)
BLOCK = {
    "calendar": "personal",
    "title": "Deep work",
    "start": START.isoformat(),
    "end": (START + timedelta(hours=1)).isoformat(),
}


def _policy(**rows: str) -> ApprovalPolicy:
    demo = load_policy()
    return ApprovalPolicy(version=1, actions={**demo.actions, **rows})


def _app(agent: Any, executors: Executors | None, policy: ApprovalPolicy | None = None) -> Any:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    graph = build_supervisor([agent], policy or load_policy(), executors=executors)
    return graph.compile(checkpointer=saver)


def _task(**inputs: Any) -> Task:
    return Task(agent="ops", goal="Triage this week's tickets.", inputs=inputs)


def _answer(out: dict[str, Any], *, approved: bool, feedback: str | None = None) -> Command[Any]:
    [intr] = out["__interrupt__"]
    decision = ApprovalDecision(
        action_id=intr.value["action_id"], approved=approved, feedback=feedback
    )
    return Command(resume=decision.model_dump())


async def test_an_auto_ticket_move_runs_in_the_first_pass() -> None:
    jira = FakeJira()
    out = await _app(TriageStubAgent(jira), fake_executors(jira)).ainvoke(
        initial_ops_state(_task()), CFG
    )

    assert "__interrupt__" not in out  # triage_ticket is "auto" in the demo policy
    [outcome] = out["executed"]
    assert (outcome.status, outcome.detail) == ("applied", "DEMO-1: moved to In Progress")
    assert outcome.action_id == out["ready"][0].id
    assert jira.issues["DEMO-1"]["status"] == "In Progress"


async def test_a_move_that_needs_approval_runs_only_after_it() -> None:
    jira = FakeJira()
    app = _app(TriageStubAgent(jira), fake_executors(jira), _policy(triage_ticket="approval"))
    out = await app.ainvoke(initial_ops_state(_task()), CFG)

    assert out["__interrupt__"][0].value["type"] == "triage_ticket"
    assert jira.writes == []
    assert out["executed"] == []

    out = await app.ainvoke(_answer(out, approved=True), CFG)

    assert [o.status for o in out["executed"]] == ["applied"]
    assert jira.issues["DEMO-1"]["status"] == "In Progress"
    assert (await app.aget_state(CFG)).next == ()


async def test_a_rejected_move_never_runs() -> None:
    jira = FakeJira()
    app = _app(TriageStubAgent(jira), fake_executors(jira), _policy(triage_ticket="approval"))
    out = await app.ainvoke(initial_ops_state(_task()), CFG)
    out = await app.ainvoke(_answer(out, approved=False), CFG)

    assert out["executed"] == []
    assert jira.writes == []


async def test_a_calendar_block_waits_for_approval_and_runs_after_it() -> None:
    jira, cal = FakeJira(), FakeCalendar()
    app = _app(TriageStubAgent(jira), fake_executors(jira, cal))
    out = await app.ainvoke(initial_ops_state(_task(block=BLOCK)), CFG)

    # the move ran ("auto"); the block waits (C3: calendar blocks need approval)
    assert [o.type for o in out["executed"]] == ["triage_ticket"]
    assert out["__interrupt__"][0].value["type"] == "schedule_calendar_block"
    assert cal.events["personal"] == []

    out = await app.ainvoke(_answer(out, approved=True), CFG)

    assert [o.type for o in out["executed"]] == ["triage_ticket", "schedule_calendar_block"]
    assert [e.title for e in cal.events["personal"]] == ["Deep work"]


async def test_approve_draft_gets_a_manual_outcome_and_nothing_runs() -> None:
    agent = ScriptedAgent([("approve_draft", "medium", "## LinkedIn post\nText.")])
    app = _app(agent, fake_executors(FakeJira()))
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    out = await app.ainvoke(_answer(out, approved=True), CFG)

    [outcome] = out["executed"]
    assert outcome.status == "manual"
    assert out["ready"][0].type == "approve_draft"


async def test_without_tools_an_executable_proposal_fails_loudly() -> None:
    with pytest.raises(AgentContractError, match="no tools configured"):
        await _app(TriageStubAgent(FakeJira()), None).ainvoke(initial_ops_state(_task()), CFG)


async def test_a_preview_that_doesnt_match_what_runs_fails_before_the_gate() -> None:
    jira = FakeJira()
    app = _app(TriageStubAgent(jira), fake_executors(jira), _policy(triage_ticket="approval"))
    with pytest.raises(AgentContractError, match="doesn't match its params"):
        await app.ainvoke(initial_ops_state(_task(tamper="Just a label tweak.")), CFG)
    assert jira.writes == []


async def test_out_of_scope_proposals_fail_before_the_gate() -> None:
    jira = FakeJira()
    app = _app(TriageStubAgent(jira), fake_executors(jira))
    with pytest.raises(AgentContractError, match="not in a configured Jira project"):
        await app.ainvoke(initial_ops_state(_task(issue="OTHER-1")), CFG)
    assert jira.writes == []


async def test_a_failed_tool_call_stops_the_run_and_a_retry_applies_it_once() -> None:
    jira = FakeJira()
    jira.fail_next, jira.fail_only = 1, {"get_issue"}  # the executor's first read
    jira.error = ToolError("fake: refused")  # not transient: no automatic retry
    app = _app(TriageStubAgent(jira), fake_executors(jira))

    with pytest.raises(ToolError, match="refused"):
        await app.ainvoke(initial_ops_state(_task()), CFG)
    snap = await app.aget_state(CFG)
    assert snap.next == ("execute",)  # stopped right there, decision and all saved
    assert jira.issues["DEMO-1"]["status"] == "To Do"

    out = await app.ainvoke(None, CFG)  # the inbox's Retry

    assert [o.status for o in out["executed"]] == ["applied"]
    assert len([w for w in jira.writes if w[0] == "transition"]) == 1


async def test_transient_failures_are_retried_by_the_node() -> None:
    jira = FakeJira()
    jira.fail_next, jira.fail_only = 2, {"get_issue"}  # 2 unreachable, then fine: < 3 tries
    jira.error = ToolUnavailableError("fake: unreachable")
    out = await _app(TriageStubAgent(jira), fake_executors(jira)).ainvoke(
        initial_ops_state(_task()), CFG
    )
    assert [o.status for o in out["executed"]] == ["applied"]


async def test_a_crash_after_the_write_is_already_applied_on_retry() -> None:
    # The write landed, then the process died before the outcome was checkpointed.
    jira = FakeJira()
    executors = fake_executors(jira)
    app = _app(TriageStubAgent(jira), executors, _policy(triage_ticket="approval"))
    out = await app.ainvoke(initial_ops_state(_task()), CFG)
    await app.ainvoke(_answer(out, approved=True), CFG, interrupt_after=["gate"])
    jira.issues["DEMO-1"]["status"] = "In Progress"  # the write that "landed"

    out = await app.ainvoke(None, CFG)

    assert [o.status for o in out["executed"]] == ["already_applied"]
    assert not [w for w in jira.writes if w[0] == "transition"]


async def test_a_revision_re_proposing_an_approved_action_does_not_run_it_twice() -> None:
    # ADR-0019: an approval stands when its round is revised; the revision may propose
    # the same change again (new action id). The executor must not apply it twice.
    jira, cal = FakeJira(), FakeCalendar()
    agent = TriageStubAgent(jira)
    policy = _policy(triage_ticket="approval")
    app = _app(agent, fake_executors(jira, cal), policy)
    out = await app.ainvoke(initial_ops_state(_task(issue="DEMO-1", block=BLOCK)), CFG)
    out = await app.ainvoke(_answer(out, approved=True), CFG)  # the move: applied
    out = await app.ainvoke(_answer(out, approved=False, feedback="Later today."), CFG)
    # revision 1 proposes the same move and the same block again
    assert agent.runs == 2
    out = await app.ainvoke(_answer(out, approved=True), CFG)  # the move, again
    calls_before = len(jira.calls)

    statuses = [(o.type, o.status) for o in out["executed"]]
    assert statuses == [("triage_ticket", "applied"), ("triage_ticket", "already_applied")]
    assert len(jira.calls) == calls_before
    assert len([w for w in jira.writes if w[0] == "transition"]) == 1


def test_router_runs_ready_actions_before_anything_else() -> None:
    from ops_fakes import make_action

    task = make_task()
    state = initial_ops_state(task)
    action = make_action(task.id)
    state["ready"] = [action]
    state["pending_actions"] = [make_action(task.id)]
    assert route_after_gate(state, 3) == "execute"
    state["executed"] = [
        ActionOutcome(action_id=action.id, type=action.type, status="manual", detail="x")
    ]
    assert route_after_gate(state, 3) == "gate"


async def test_an_action_id_from_an_earlier_round_is_refused() -> None:
    # Review: outcomes are keyed by action id, so a reused id would look already executed
    # and an approved action would silently never run.
    from ops_fakes import make_action

    class ReusingAgent:
        name = "echo"

        def build(self) -> Any:
            async def propose(state: _S) -> dict[str, Any]:
                task = state["task"]
                auto = make_action(task.id, id="same-id", type="draft_content", preview="Draft.")
                ask = make_action(task.id, type="publish_public", preview="Post.")
                result = AgentResult(
                    task_id=task.id,
                    agent="echo",
                    status="succeeded",
                    summary="x",
                    actions=(auto, ask),
                )
                return {"result": result}

            builder = StateGraph(_S, input_schema=AgentInput, output_schema=AgentOutput)
            builder.add_node("propose", propose)
            builder.add_edge(GRAPH_START, "propose")
            builder.add_edge("propose", END)
            return builder.compile()

    app = _app(ReusingAgent(), None)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    assert [o.action_id for o in out["executed"]] == ["same-id"]

    with pytest.raises(AgentContractError, match=r"reused action ids \['same-id'\]"):
        await app.ainvoke(_answer(out, approved=False, feedback="Shorter."), CFG)
