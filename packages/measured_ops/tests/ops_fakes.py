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


# What the eval node writes; tests that call policy.decide directly use these.
PASSED = EvalScores({"quality": 1.0, "injection": 1.0, "pii": 1.0}, evaluator="test")
FAILED = EvalScores(
    {"quality": 1.0, "injection": 1.0, "pii": 0.0}, evaluator="test", reasons={"pii": "email found"}
)


def make_action(task_id: str, **overrides: Any) -> PendingAction:
    fields: dict[str, Any] = {
        "task_id": task_id,
        "type": "publish_public",
        "target": "linkedin",
        "preview": "A draft post.",
        "risk_level": "medium",
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


class FakeEchoAgent:
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


class NoOutputSchemaAgent:
    """Declares its input but not its output, so private keys would leak out."""

    name = "no_output"

    def build(self) -> AgentGraph:
        builder = StateGraph(_EchoState, input_schema=AgentInput)
        builder.add_node("draft", _draft)
        builder.add_node("propose", _propose)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", "propose")
        builder.add_edge("propose", END)
        return builder.compile()  # type: ignore[return-value]


class SelfScoringAgent(FakeEchoAgent):
    """Breaks the contract: attaches its own eval scores to the action it proposes."""

    def build(self) -> AgentGraph:
        async def propose(state: _EchoState) -> dict[str, Any]:
            task = state["task"]
            action = make_action(task.id, eval_scores=EvalScores({"quality": 1.0}))
            result = AgentResult(
                task_id=task.id, agent="echo", status="succeeded", summary="x", actions=(action,)
            )
            return {"result": result}

        builder = StateGraph(_EchoState, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("propose", propose)
        builder.add_edge(START, "propose")
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


class ScriptedAgent:
    """Proposes the actions it was given, as (type, risk_level[, preview[, target]])
    tuples, or fails.

    Counts how often its graph runs, so tests can prove the supervisor never re-runs
    an agent when the approval gate resumes.
    """

    def __init__(
        self,
        actions: list[tuple[str, ...]] | None = None,
        *,
        name: str = "echo",
        fail: bool = False,
    ) -> None:
        self.name = name
        self.specs = actions or []
        self.fail = fail
        self.runs = 0
        self.tasks: list[Task] = []  # every task it was given, in order

    def build(self) -> AgentGraph:
        agent = self

        async def propose(state: _EchoState) -> dict[str, Any]:
            agent.runs += 1
            task = state["task"]
            agent.tasks.append(task)
            if agent.fail:
                result = AgentResult(
                    task_id=task.id, agent=agent.name, status="failed", summary="", error="boom"
                )
                return {"result": result}
            actions = tuple(
                make_action(
                    task.id,
                    type=spec[0],
                    risk_level=spec[1],
                    preview=spec[2] if len(spec) > 2 else f"{spec[0]} #{i}",
                    target=spec[3] if len(spec) > 3 else "linkedin",
                )
                for i, spec in enumerate(agent.specs)
            )
            result = AgentResult(
                task_id=task.id,
                agent=agent.name,
                status="succeeded",
                summary="Scripted.",
                actions=actions,
            )
            return {"result": result}

        builder = StateGraph(_EchoState, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("propose", propose)
        builder.add_edge(START, "propose")
        builder.add_edge("propose", END)
        return builder.compile()


class LLMAgent:
    """Calls a fake chat model once per scripted reply, then proposes an auto action.

    Its node also records the config it ran with, so tests can check the tracing
    names and metadata the supervisor gives each agent run.
    """

    def __init__(
        self, replies: list[Any], *, name: str = "echo", action_type: str = "draft_content"
    ) -> None:
        from langchain_core.language_models import GenericFakeChatModel

        self.name = name
        self.model = GenericFakeChatModel(messages=iter(replies))
        self.calls = len(replies)
        self.action_type = action_type
        self.seen_config: dict[str, Any] = {}

    def build(self) -> AgentGraph:
        from langchain_core.runnables import RunnableConfig

        agent = self

        async def work(state: _EchoState, config: RunnableConfig) -> dict[str, Any]:
            agent.seen_config = {"tags": config.get("tags"), "metadata": config.get("metadata")}
            for _ in range(agent.calls):
                # its own config, without callbacks, like proofline's adapter
                await agent.model.ainvoke("hi", config={"run_name": "llm", "tags": ["inner"]})
            task = state["task"]
            action = make_action(task.id, type=agent.action_type, risk_level="low", preview="ok")
            result = AgentResult(
                task_id=task.id,
                agent=agent.name,
                status="succeeded",
                summary="x",
                actions=(action,),
            )
            return {"result": result}

        builder = StateGraph(_EchoState, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("work", work)
        builder.add_edge(START, "work")
        builder.add_edge("work", END)
        return builder.compile()
