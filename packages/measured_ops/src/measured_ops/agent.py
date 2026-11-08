"""The agent interface: every agent is a LangGraph subgraph, `Task` in, `AgentResult` out.

An agent builds a `StateGraph` over its own private state, declaring
`input_schema=AgentInput` and `output_schema=AgentOutput`:

    builder = StateGraph(MyState, input_schema=AgentInput, output_schema=AgentOutput)

LangGraph then accepts only `{"task": ...}` as input and returns only
`{"result": ...}`, however many internal fields the agent uses. The supervisor (C2)
calls it through `run_agent`, so agents never share state keys with the supervisor
or with each other, and an agent's internals can change without touching the contract.

Agents propose actions in `AgentResult.actions`; they never execute anything that
leaves the system. The supervisor gates each action through the approval policy.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from measured_ops.contracts import AgentResult, Task


class AgentInput(TypedDict):
    task: Task


class AgentOutput(TypedDict):
    result: AgentResult


AgentGraph = CompiledStateGraph[Any, Any, AgentInput, AgentOutput]


class AgentContractError(RuntimeError):
    """An agent's graph or result breaks the C1 contract."""


@runtime_checkable
class Agent(Protocol):
    """A specialist agent. `name` matches `Task.agent` for the tasks it takes."""

    @property
    def name(self) -> str: ...

    def build(self) -> AgentGraph:
        """Compile the agent's subgraph. Called once by the supervisor at startup.

        Compile without a checkpointer: run inside the supervisor, the subgraph
        uses the parent's.
        """
        ...


def check_agent_graph(graph: AgentGraph) -> None:
    """Fail fast if `graph` doesn't declare the Task → AgentResult schemas."""
    inputs = set(graph.get_input_jsonschema().get("properties", {}))
    outputs = set(graph.get_output_jsonschema().get("properties", {}))
    if inputs != {"task"}:
        raise AgentContractError(
            f"agent input must be exactly {{'task'}}, got {sorted(inputs)}: "
            "pass input_schema=AgentInput to StateGraph"
        )
    if outputs != {"result"}:
        raise AgentContractError(
            f"agent output must be exactly {{'result'}}, got {sorted(outputs)}: "
            "pass output_schema=AgentOutput to StateGraph"
        )


async def run_agent(
    agent: Agent,
    graph: AgentGraph,
    task: Task,
    *,
    config: RunnableConfig | None = None,
    context: Any = None,
) -> AgentResult:
    """Run one task through an agent's compiled graph and check the result belongs to it."""
    if task.agent != agent.name:
        raise AgentContractError(f"task {task.id} is for {task.agent!r}, not {agent.name!r}")
    out = await graph.ainvoke({"task": task}, config, context=context)
    result = out.get("result") if isinstance(out, dict) else None
    if not isinstance(result, AgentResult):
        raise AgentContractError(
            f"agent {agent.name!r} returned no AgentResult for task {task.id}: {out!r}"
        )
    if result.task_id != task.id or result.agent != agent.name:
        raise AgentContractError(
            f"agent {agent.name!r} returned a result for task {result.task_id} "
            f"(agent {result.agent!r}), expected task {task.id}"
        )
    self_scored = [a.id for a in result.actions if a.eval_scores is not None]
    if self_scored:
        raise AgentContractError(
            f"agent {agent.name!r} scored its own actions {self_scored}: "
            "eval_scores are written only by the eval node"
        )
    return result
