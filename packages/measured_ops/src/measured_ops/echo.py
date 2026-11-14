"""The stub echo agent (C2): drafts the task's goal and proposes publishing it.

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


async def _propose(state: _State) -> dict[str, Any]:
    task = state["task"]
    action = PendingAction(
        task_id=task.id,
        type="publish_public",
        target="linkedin",
        preview=state["draft"],
        risk_level="medium",
    )
    result = AgentResult(
        task_id=task.id,
        agent="echo",
        status="succeeded",
        summary="Drafted the goal as a post.",
        actions=(action,),
    )
    return {"result": result}


class EchoAgent:
    name = "echo"

    def build(self) -> AgentGraph:
        builder = StateGraph(_State, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("draft", _draft)
        builder.add_node("propose", _propose)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", "propose")
        builder.add_edge("propose", END)
        return builder.compile()
