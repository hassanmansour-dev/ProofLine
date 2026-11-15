"""Revisions in the same thread (C8, ADR-0019): gate → revise → call_agent.

A rejection with feedback becomes the next Task (parent_id, feedback, the rejected
preview and a revision number in its inputs), at most `max_revisions` times, counted
from the task, not from the results. Generic agents only: the supervisor knows nothing
about drafts.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from ops_fakes import ScriptedAgent, make_task

from measured_ops import ApprovalDecision, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio

ONE = [("publish_public", "medium", "Draft one.")]


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _app(agent: ScriptedAgent, **kwargs: Any) -> Any:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    return build_supervisor([agent], load_policy(), **kwargs).compile(checkpointer=saver)


CFG: Any = {"configurable": {"thread_id": "r"}, "recursion_limit": 40}


def _answer(out: dict[str, Any], *, approved: bool, feedback: str | None = None) -> Command[Any]:
    action_id = out["__interrupt__"][0].value["action_id"]
    decision = ApprovalDecision(action_id=action_id, approved=approved, feedback=feedback)
    return Command(resume=decision.model_dump())


async def test_rejection_with_feedback_revises_in_the_same_thread() -> None:
    agent = ScriptedAgent(ONE)
    app = _app(agent)
    first = make_task(inputs={"topic": "loops"})

    out = await app.ainvoke(initial_ops_state(first), CFG)
    out = await app.ainvoke(_answer(out, approved=False, feedback="Shorter."), CFG)

    assert "__interrupt__" in out  # paused again, on the revised draft
    assert agent.runs == 2
    revision = agent.tasks[1]
    assert revision.parent_id == first.id
    assert revision.feedback == "Shorter."
    assert (revision.agent, revision.goal) == (first.agent, first.goal)
    assert revision.inputs == {"topic": "loops", "previous_preview": "Draft one.", "revision": 1}
    assert out["task"].id == revision.id
    assert len(out["results"]) == 2
    assert [d.approved for d in out["decisions"]] == [False]


async def test_rejection_without_feedback_ends_the_run() -> None:
    agent = ScriptedAgent(ONE)
    app = _app(agent)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)

    done = await app.ainvoke(_answer(out, approved=False), CFG)

    assert "__interrupt__" not in done
    assert agent.runs == 1


async def test_approval_after_a_revision_ends_ready() -> None:
    agent = ScriptedAgent(ONE)
    app = _app(agent)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    out = await app.ainvoke(_answer(out, approved=False, feedback="Again."), CFG)

    done = await app.ainvoke(_answer(out, approved=True), CFG)

    assert "__interrupt__" not in done
    assert [a.task_id for a in done["ready"]] == [agent.tasks[1].id]
    assert agent.runs == 2


async def test_revisions_stop_at_the_cap_counted_from_the_task() -> None:
    agent = ScriptedAgent(ONE)
    app = _app(agent)  # default max_revisions=3
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    for n in range(3):
        out = await app.ainvoke(_answer(out, approved=False, feedback=f"Fix {n}."), CFG)
        assert "__interrupt__" in out

    done = await app.ainvoke(_answer(out, approved=False, feedback="Still no."), CFG)

    assert "__interrupt__" not in done
    assert agent.runs == 4
    assert [t.inputs.get("revision", 0) for t in agent.tasks] == [0, 1, 2, 3]


async def test_the_cap_reads_the_tasks_revision_number_not_the_results() -> None:
    # A thread may hold several tasks once C6 creates them: only the task counts.
    agent = ScriptedAgent(ONE)
    app = _app(agent, max_revisions=3)
    out = await app.ainvoke(initial_ops_state(make_task(inputs={"revision": 3})), CFG)

    done = await app.ainvoke(_answer(out, approved=False, feedback="More."), CFG)

    assert "__interrupt__" not in done
    assert agent.runs == 1


@pytest.mark.parametrize("bad", ["two", -1, True, 1.5])
async def test_a_bad_revision_number_fails_closed_no_revision(bad: Any) -> None:
    agent = ScriptedAgent(ONE)
    app = _app(agent)
    out = await app.ainvoke(initial_ops_state(make_task(inputs={"revision": bad})), CFG)

    done = await app.ainvoke(_answer(out, approved=False, feedback="More."), CFG)

    assert "__interrupt__" not in done
    assert agent.runs == 1


async def test_max_revisions_zero_never_revises() -> None:
    agent = ScriptedAgent(ONE)
    app = _app(agent, max_revisions=0)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)

    done = await app.ainvoke(_answer(out, approved=False, feedback="More."), CFG)

    assert "__interrupt__" not in done


async def test_several_rejections_in_one_round_revise_once_with_everything() -> None:
    agent = ScriptedAgent(
        [("publish_public", "medium", "Post A."), ("publish_public", "medium", "Post B.")]
    )
    app = _app(agent)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    out = await app.ainvoke(_answer(out, approved=False, feedback="A: shorter."), CFG)
    assert agent.runs == 1  # the second action is still waiting: no revision yet

    out = await app.ainvoke(_answer(out, approved=False, feedback="B: punchier."), CFG)

    assert agent.runs == 2
    revision = agent.tasks[1]
    assert revision.feedback == "A: shorter.\n\nB: punchier."
    assert revision.inputs["previous_preview"] == "Post A.\n\n---\n\nPost B."


async def test_one_round_revises_once_even_after_a_refused_answer() -> None:
    # The gate stays idempotent on resume: a refused answer changes nothing, and the
    # next valid rejection revises exactly once.
    agent = ScriptedAgent(ONE)
    app = _app(agent)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    wrong = ApprovalDecision(action_id="not-the-action", approved=False, feedback="x")
    with pytest.raises(Exception, match="not the action waiting"):
        await app.ainvoke(Command(resume=wrong.model_dump()), CFG)

    out = await app.ainvoke(_answer(out, approved=False, feedback="Shorter."), CFG)

    assert agent.runs == 2
    assert len(out["results"]) == 2
    assert len(out["decisions"]) == 1


def test_graph_has_the_revise_node_and_its_edges() -> None:
    mermaid = (
        build_supervisor([ScriptedAgent()], load_policy()).compile().get_graph().draw_mermaid()
    )
    assert "revise" in mermaid
    assert "gate -.-> revise" in mermaid
    assert "revise --> call_agent" in mermaid


async def test_an_approved_action_stays_ready_when_its_round_is_revised() -> None:
    # ADR-0019: an approval stands. The revision is a new task; its actions are new.
    agent = ScriptedAgent(
        [("publish_public", "medium", "Post A."), ("publish_public", "medium", "Post B.")]
    )
    app = _app(agent)
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    out = await app.ainvoke(_answer(out, approved=False, feedback="A: shorter."), CFG)

    out = await app.ainvoke(_answer(out, approved=True), CFG)

    assert agent.runs == 2
    assert [a.preview for a in out["ready"]] == ["Post B."]
    assert {a.task_id for a in out["pending_actions"]} == {agent.tasks[1].id}


async def test_an_agent_that_reuses_a_decided_action_id_fails_loudly() -> None:
    # Review nit: rejected_with_feedback keys on action ids, so a reused id could match
    # an earlier round's rejection and revise again. Fixed ids are refused.
    from typing import Any as _Any

    from langgraph.graph import END, START, StateGraph
    from ops_fakes import _EchoState, make_action

    from measured_ops import AgentInput, AgentOutput, AgentResult
    from measured_ops.agent import AgentContractError

    class FixedIdAgent:
        name = "echo"

        def build(self) -> _Any:
            async def propose(state: _EchoState) -> dict[str, _Any]:
                task = state["task"]
                action = make_action(task.id, id="post-1")
                return {
                    "result": AgentResult(
                        task_id=task.id,
                        agent="echo",
                        status="succeeded",
                        summary="x",
                        actions=(action,),
                    )
                }

            b = StateGraph(_EchoState, input_schema=AgentInput, output_schema=AgentOutput)
            b.add_node("propose", propose)
            b.add_edge(START, "propose")
            b.add_edge("propose", END)
            return b.compile()

    app = _app(FixedIdAgent())  # type: ignore[arg-type]
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)

    with pytest.raises(AgentContractError, match="post-1"):
        await app.ainvoke(_answer(out, approved=False, feedback="Again."), CFG)
