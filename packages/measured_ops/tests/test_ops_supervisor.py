"""The supervisor graph (C2): agent → triage → approval gate, against an in-memory
checkpointer with the strict serde allowlist. Postgres and the restart case live in
test_ops_e2e.py.

Demo policy rows used here: publish_public = approval, draft_content = auto,
bulk_export_personal_data = never.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from ops_fakes import LeakyAgent, ScriptedAgent, make_action, make_task
from pydantic import ValidationError

from measured_ops import (
    ActionBlockedError,
    AgentContractError,
    ApprovalDecision,
    ApprovalPolicy,
    initial_ops_state,
    load_policy,
)
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.supervisor import build_supervisor, route_pending

pytestmark = pytest.mark.anyio

APPROVAL = ("publish_public", "medium")
AUTO = ("draft_content", "low")
NEVER = ("bulk_export_personal_data", "low")


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _app(*agents: Any) -> Any:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    return build_supervisor(list(agents), load_policy()).compile(checkpointer=saver)


def _cfg(thread: str = "t") -> Any:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 40}


def _resume(action_id: str, *, approved: bool, feedback: str | None = None) -> Command[Any]:
    decision = ApprovalDecision(action_id=action_id, approved=approved, feedback=feedback)
    return Command(resume=decision.model_dump())


async def _start(app: Any, agent_name: str = "echo") -> dict[str, Any]:
    out: dict[str, Any] = await app.ainvoke(initial_ops_state(make_task(agent=agent_name)), _cfg())
    return out


# ---- the approval path ---------------------------------------------------------------


async def test_action_needing_approval_pauses_with_its_preview() -> None:
    app = _app(ScriptedAgent([APPROVAL]))

    out = await _start(app)

    [intr] = out["__interrupt__"]
    action = out["pending_actions"][0]
    assert intr.value == {
        "action_id": action.id,
        "task_id": action.task_id,
        "type": "publish_public",
        "target": "linkedin",
        "preview": "publish_public #0",
        "risk_level": "medium",
    }
    snap = await app.aget_state(_cfg())
    assert snap.next == ("gate",)


async def test_approve_moves_the_action_to_ready_exactly_once() -> None:
    agent = ScriptedAgent([APPROVAL])
    app = _app(agent)
    action_id = (await _start(app))["__interrupt__"][0].value["action_id"]

    done = await app.ainvoke(_resume(action_id, approved=True), _cfg())

    assert "__interrupt__" not in done
    assert [a.id for a in done["ready"]] == [action_id]  # once, despite the gate re-running
    assert [(d.action_id, d.approved) for d in done["decisions"]] == [(action_id, True)]
    assert done["pending_actions"] == []
    assert done["blocked"] == []
    assert agent.runs == 1  # resuming re-runs the gate, never the agent


async def test_reject_records_the_decision_and_feedback_and_stops() -> None:
    app = _app(ScriptedAgent([APPROVAL]))
    action_id = (await _start(app))["__interrupt__"][0].value["action_id"]

    done = await app.ainvoke(_resume(action_id, approved=False, feedback="Too long."), _cfg())

    assert done["ready"] == []
    assert done["blocked"] == []
    [decision] = done["decisions"]
    assert (decision.approved, decision.feedback) == (False, "Too long.")
    assert done["pending_actions"] == []


async def test_two_actions_pause_one_at_a_time_in_order() -> None:
    app = _app(ScriptedAgent([APPROVAL, APPROVAL]))
    first = (await _start(app))["__interrupt__"][0].value

    second = (await app.ainvoke(_resume(first["action_id"], approved=True), _cfg()))[
        "__interrupt__"
    ][0].value
    done = await app.ainvoke(_resume(second["action_id"], approved=False), _cfg())

    assert (first["preview"], second["preview"]) == ("publish_public #0", "publish_public #1")
    assert [a.id for a in done["ready"]] == [first["action_id"]]
    assert [d.approved for d in done["decisions"]] == [True, False]


# ---- the policy: auto, never, high risk ---------------------------------------------


async def test_auto_action_is_ready_without_pausing() -> None:
    out = await _start(_app(ScriptedAgent([AUTO])))

    assert "__interrupt__" not in out
    assert [a.type for a in out["ready"]] == ["draft_content"]
    assert out["decisions"] == []


async def test_never_action_is_blocked_before_the_gate(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="measured_ops"):
        out = await _start(_app(ScriptedAgent([NEVER])))

    assert "__interrupt__" not in out
    assert [a.type for a in out["blocked"]] == ["bulk_export_personal_data"]
    assert out["ready"] == []
    assert any("bulk_export_personal_data" in r.getMessage() for r in caplog.records)


async def test_mixed_actions_pause_once_and_land_in_the_right_lists() -> None:
    app = _app(ScriptedAgent([AUTO, APPROVAL, NEVER]))

    out = await _start(app)
    [intr] = out["__interrupt__"]
    done = await app.ainvoke(_resume(intr.value["action_id"], approved=True), _cfg())

    assert intr.value["type"] == "publish_public"
    assert [a.type for a in done["ready"]] == ["draft_content", "publish_public"]
    assert [a.type for a in done["blocked"]] == ["bulk_export_personal_data"]


async def test_high_risk_pauses_even_on_an_auto_row() -> None:
    out = await _start(_app(ScriptedAgent([("draft_content", "high")])))

    assert out["__interrupt__"][0].value["type"] == "draft_content"
    assert out["ready"] == []


# ---- the gate refuses bad answers and stays paused -----------------------------------


async def test_answer_for_another_action_is_refused_and_the_run_stays_paused() -> None:
    app = _app(ScriptedAgent([APPROVAL]))
    action_id = (await _start(app))["__interrupt__"][0].value["action_id"]

    with pytest.raises(ValueError, match="not the action waiting"):
        await app.ainvoke(_resume("someone-else", approved=True), _cfg())

    snap = await app.aget_state(_cfg())
    assert snap.next == ("gate",)
    assert snap.values["ready"] == []
    done = await app.ainvoke(_resume(action_id, approved=True), _cfg())
    assert [a.id for a in done["ready"]] == [action_id]


async def test_no_approval_unblocks_an_action_the_policy_now_forbids() -> None:
    # Paused under one policy, resumed under a stricter one (e.g. a new process with
    # the private table): the gate's own check_decision is the last line of defence.
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    demo = load_policy()
    strict = ApprovalPolicy(version=1, actions={**demo.actions, "publish_public": "never"})
    paused = build_supervisor([ScriptedAgent([APPROVAL])], demo).compile(checkpointer=saver)
    action_id = (await _start(paused))["__interrupt__"][0].value["action_id"]
    app = build_supervisor([ScriptedAgent([APPROVAL])], strict).compile(checkpointer=saver)

    with pytest.raises(ActionBlockedError):
        await app.ainvoke(_resume(action_id, approved=True), _cfg())

    snap = await app.aget_state(_cfg())
    assert snap.next == ("gate",)
    assert snap.values["ready"] == []
    done = await app.ainvoke(_resume(action_id, approved=False), _cfg())
    assert done["ready"] == []
    assert [(d.action_id, d.approved) for d in done["decisions"]] == [(action_id, False)]


async def test_malformed_answer_is_refused_and_the_run_stays_paused() -> None:
    app = _app(ScriptedAgent([APPROVAL]))
    action_id = (await _start(app))["__interrupt__"][0].value["action_id"]

    with pytest.raises(ValidationError, match="action_id"):
        await app.ainvoke(Command(resume={"approved": "maybe"}), _cfg())

    assert (await app.aget_state(_cfg())).next == ("gate",)
    done = await app.ainvoke(_resume(action_id, approved=False), _cfg())
    assert [d.action_id for d in done["decisions"]] == [action_id]


async def test_the_pause_publishes_the_answer_schema() -> None:
    out = await _start(_app(ScriptedAgent([APPROVAL])))

    schema = out["__interrupt__"][0].response_schema
    assert {"action_id", "approved"} <= set(schema["required"])


def test_route_pending_is_pure() -> None:
    task = make_task()
    assert route_pending(initial_ops_state(task)) == "__end__"
    state = initial_ops_state(task)
    state["pending_actions"] = [make_action(task.id)]
    assert route_pending(state) == "gate"


# ---- agents --------------------------------------------------------------------------


async def test_failed_agent_result_is_recorded_without_pausing() -> None:
    out = await _start(_app(ScriptedAgent(fail=True)))

    assert "__interrupt__" not in out
    assert [r.status for r in out["results"]] == ["failed"]
    assert out["ready"] == out["blocked"] == out["pending_actions"] == []


async def test_task_for_an_unknown_agent_fails_loudly() -> None:
    with pytest.raises(AgentContractError, match="no agent named 'writer'"):
        await _start(_app(ScriptedAgent()), agent_name="writer")


def test_agents_are_checked_when_the_supervisor_is_built() -> None:
    with pytest.raises(AgentContractError, match="duplicate agent name"):
        build_supervisor([ScriptedAgent(), ScriptedAgent()], load_policy())
    with pytest.raises(AgentContractError, match="agent input must be"):
        build_supervisor([LeakyAgent()], load_policy())


def test_graph_shape() -> None:
    graph = build_supervisor([ScriptedAgent()], load_policy()).compile().get_graph()
    assert set(graph.nodes) == {"__start__", "call_agent", "triage", "gate", "__end__"}
