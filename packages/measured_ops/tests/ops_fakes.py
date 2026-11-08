"""Test-only agent and model builders for the C1 contract tests."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from measured_evals import EvalScores
from measured_ops import AgentGraph, AgentInput, AgentOutput, AgentResult, PendingAction, Task


def make_task(**overrides: Any) -> Task:
    fields: dict[str, Any] = {"agent": "echo", "goal": "Echo the topic back as a draft."}
    fields.update(overrides)
    return Task(**fields)


def make_action(task_id: str, **overrides: Any) -> PendingAction:
    fields: dict[str, Any] = {
        "task_id": task_id,
        "type": "publish_public",
        "target": "linkedin",
        "preview": "A draft post.",
        "risk_level": "medium",
        "eval_scores": EvalScores({"quality": 0.8}),
    }
    fields.update(overrides)
    return PendingAction(**fields)


class _EchoState(TypedDict, total=False):
    task: Task
    text: str  # private to the agent: must never leak into its output
    result: AgentResult


async def _draft(state: _EchoState) -> dict[str, Any]:
    return {"text": f"Draft: {state['task'].goal}"}


async def _propose(state: _EchoState) -> dict[str, Any]:
    task = state["task"]
    action = make_action(task.id, preview=state["text"])
    return {
        "result": AgentResult(
            task_id=task.id, agent="echo", status="succeeded", summary="Echoed.", actions=(action,)
        )
    }


class EchoAgent:
    """Two private nodes; proposes one publish action. Conforms to `measured_ops.Agent`."""

    name = "echo"

    def build(self) -> AgentGraph:
        builder = StateGraph(_EchoState, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("draft", _draft)
        builder.add_node("propose", _propose)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", "propose")
        builder.add_edge("propose", END)
        return builder.compile()


class LeakyAgent:
    """Breaks the contract: no input/output schemas, so its whole state is its interface."""

    name = "leaky"

    def build(self) -> AgentGraph:
        builder = StateGraph(_EchoState)
        builder.add_node("draft", _draft)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", END)
        return builder.compile()  # type: ignore[return-value]
