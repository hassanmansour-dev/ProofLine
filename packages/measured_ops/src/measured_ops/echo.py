"""The stub echo agent (C2): drafts the task's goal and proposes publishing it.

`action_type` (default "publish_public") lets the e2e tests propose an "auto" action
too, to exercise the eval node's escalation (C7).

It exists to exercise the supervisor end to end (the CI e2e test). The
supervisor never imports it: like every agent, it is passed in (ADR-0014).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from measured_ops.agent import AgentGraph, AgentInput, AgentOutput
from measured_ops.contracts import AgentResult, PendingAction, Task


class _State(TypedDict, total=False):
    task: Task
    draft: str  # private: never leaves the agent
    result: AgentResult


async def _draft(state: _State) -> dict[str, Any]:
    return {"draft": f"Draft post: {state['task'].goal}"}


def _proposer(action_type: str) -> Any:
    async def _propose(state: _State) -> dict[str, Any]:
        return {"result": _result(state, action_type)}

    return _propose


def _result(state: _State, action_type: str) -> AgentResult:
    task = state["task"]
    action = PendingAction(
        task_id=task.id,
        type=action_type,
        target="linkedin",
        preview=state["draft"],
        risk_level="medium",
    )
    return AgentResult(
        task_id=task.id,
        agent="echo",
        status="succeeded",
        summary="Drafted the goal as a post.",
        actions=(action,),
    )


class EchoAgent:
    name = "echo"

    def __init__(self, action_type: str = "publish_public") -> None:
        self.action_type = action_type

    def build(self) -> AgentGraph:
        builder = StateGraph(_State, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("draft", _draft)
        builder.add_node("propose", _proposer(self.action_type))
        builder.add_edge(START, "draft")
        builder.add_edge("draft", "propose")
        builder.add_edge("propose", END)
        return builder.compile()
