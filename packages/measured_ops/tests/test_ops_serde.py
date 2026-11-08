"""Every OpsState value survives a checkpoint under strict msgpack (ADR-0006).

C2 wires the real checkpointer; this pins the allowlist the supervisor must pass it.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from ops_fakes import make_action, make_task

from measured_evals import EvalScores
from measured_ops import AgentResult, ApprovalDecision, OpsState
from measured_ops.contracts import SERDE_ALLOWLIST

pytestmark = pytest.mark.anyio

TASK = make_task(inputs={"topic": "evals", "tags": ["a", "b"]})
ACTION = make_action(TASK.id, eval_scores=EvalScores({"quality": 0.8}))


async def _write(state: OpsState) -> dict[str, Any]:
    result = AgentResult(
        task_id=TASK.id, agent="echo", status="succeeded", summary="ok", actions=(ACTION,)
    )
    decision = ApprovalDecision(action_id=ACTION.id, approved=False, feedback="Shorter.")
    return {"results": [result], "pending_actions": [ACTION], "decisions": [decision]}


def _graph(saver: InMemorySaver) -> Any:
    builder = StateGraph(OpsState)
    builder.add_node("write", _write)
    builder.add_edge(START, "write")
    builder.add_edge("write", END)
    return builder.compile(checkpointer=saver)


async def _round_trip(serde: JsonPlusSerializer) -> dict[str, Any]:
    saver = InMemorySaver(serde=serde)
    cfg: Any = {"configurable": {"thread_id": "t1"}}
    await _graph(saver).ainvoke({"task": TASK}, cfg)
    return dict((await _graph(saver).aget_state(cfg)).values)


async def test_ops_state_survives_strict_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    values = await _round_trip(JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    assert values["task"] == TASK
    assert values["pending_actions"] == [ACTION]
    action = values["results"][0].actions[0]
    assert isinstance(action.eval_scores, EvalScores)
    assert action.eval_scores.passed is True
    assert values["decisions"][0].feedback == "Shorter."


async def test_strict_mode_blocks_unlisted_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    values = await _round_trip(JsonPlusSerializer(allowed_msgpack_modules=[]))
    assert not isinstance(values.get("task"), type(TASK))  # proves the test above means something
