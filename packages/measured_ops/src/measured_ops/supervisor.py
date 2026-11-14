"""The supervisor graph (C2, ADR-0016): one task → its agent → triage → approval gate.

    START ─▶ call_agent ─▶ triage ─┬─▶ gate ─┐  (one pending action per pass)
                                   │    ▲    │
                                   │    └────┤
                                   └─────────┴─▶ END

- `call_agent` runs the task through the injected agent's subgraph (`run_agent`)
  and appends its `AgentResult`. Agents are passed in, never imported (ADR-0014).
- `triage` classifies every proposed action with the policy, once: "auto" → `ready`,
  "never" → `blocked` (logged by the policy), "approval" → `pending_actions`. It
  runs before the gate because `policy.decide` logs, and the gate re-runs on resume.
- `gate` pauses on the head of `pending_actions` with `interrupt()`. On resume it
  checks the `ApprovalDecision` (right action, never unblocks a "never") inside
  `interrupt()` itself, then moves the action to `ready` or records the rejection.
  Nothing before the `interrupt()` call has side effects.

Nothing in `ready` executes yet: tools arrive in C5. A rejection ends the run with
the decision recorded; turning feedback into a revision `Task` lands with the
content agent (C8).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import AfterValidator

from measured_ops.agent import Agent, AgentContractError, AgentGraph, check_agent_graph, run_agent
from measured_ops.contracts import ApprovalDecision, OpsState, PendingAction
from measured_ops.policy import ApprovalPolicy

log = logging.getLogger("measured_ops.supervisor")

SupervisorGraph = StateGraph[OpsState, None, OpsState, OpsState]


def route_pending(state: OpsState) -> Literal["gate", "__end__"]:
    """Pure router: back to the gate while anything waits for a human."""
    return "gate" if state.get("pending_actions") else "__end__"


def gate_payload(action: PendingAction) -> dict[str, Any]:
    """What the human (CLI now, inbox in C4) sees: JSON-safe, no eval scores yet (C7)."""
    return {
        "action_id": action.id,
        "task_id": action.task_id,
        "type": action.type,
        "target": action.target,
        "preview": action.preview,
        "risk_level": action.risk_level,
    }


def answer_schema(action: PendingAction, policy: ApprovalPolicy) -> Any:
    """The resume value the gate accepts for `action`: an `ApprovalDecision` for this
    action that doesn't approve a "never" action.

    Checked as `interrupt(response_schema=...)`, not after `interrupt()` returns: a
    resume value `interrupt()` returns is saved as the task's resume write, so a bad
    answer rejected later would be replayed on every following resume. Validation
    inside `interrupt()` fails before anything is saved, and the gate stays paused
    for a correct answer (ADR-0016). The state still holds a plain `ApprovalDecision`.
    """

    def _for_this_action(decision: ApprovalDecision) -> ApprovalDecision:
        if decision.action_id != action.id:
            raise ValueError(
                f"decision is for action {decision.action_id}, not the action waiting "
                f"at the gate ({action.id})"
            )
        policy.check_decision(action, decision)  # raises ActionBlockedError for "never"
        return decision

    return Annotated[ApprovalDecision, AfterValidator(_for_this_action)]


def build_supervisor(agents: Sequence[Agent], policy: ApprovalPolicy) -> SupervisorGraph:
    """Wire the supervisor. Compiles and checks every agent's graph now, so a broken
    agent fails at startup, not mid-run. Compile the result with a checkpointer from
    `measured_ops.checkpoint.open_checkpointer` (the gate needs one to resume)."""
    graphs: dict[str, tuple[Agent, AgentGraph]] = {}
    for agent in agents:
        if agent.name in graphs:
            raise AgentContractError(f"duplicate agent name {agent.name!r}")
        graph = agent.build()
        check_agent_graph(graph)
        graphs[agent.name] = (agent, graph)

    async def call_agent(state: OpsState) -> dict[str, Any]:
        task = state["task"]
        if task.agent not in graphs:
            raise AgentContractError(
                f"no agent named {task.agent!r} for task {task.id}; known agents: {sorted(graphs)}"
            )
        agent, graph = graphs[task.agent]
        result = await run_agent(agent, graph, task)
        log.info(
            "supervisor: task %s → agent %r %s with %d action(s)",
            task.id,
            agent.name,
            result.status,
            len(result.actions),
        )
        return {"results": [result]}

    async def triage(state: OpsState) -> dict[str, Any]:
        result = state["results"][-1]
        ready: list[PendingAction] = []
        blocked: list[PendingAction] = []
        pending: list[PendingAction] = []
        for action in result.actions:
            decision = policy.decide(action)
            {"auto": ready, "never": blocked, "approval": pending}[decision].append(action)
        return {"ready": ready, "blocked": blocked, "pending_actions": pending}

    async def gate(state: OpsState) -> dict[str, Any]:
        # Re-runs from here on resume: nothing above interrupt() may have side effects.
        pending = state["pending_actions"]
        action = pending[0]
        decision: ApprovalDecision = interrupt(
            gate_payload(action), response_schema=answer_schema(action, policy)
        )
        log.info(
            "supervisor: action %s (%s) %s",
            action.id,
            action.type,
            "approved" if decision.approved else "rejected",
        )
        return {
            "decisions": [decision],
            "pending_actions": pending[1:],
            "ready": [action] if decision.approved else [],
        }

    builder: SupervisorGraph = StateGraph(OpsState)
    builder.add_node("call_agent", call_agent)
    builder.add_node("triage", triage)
    builder.add_node("gate", gate)
    builder.add_edge(START, "call_agent")
    builder.add_edge("call_agent", "triage")
    builder.add_conditional_edges("triage", route_pending, {"gate": "gate", END: END})
    builder.add_conditional_edges("gate", route_pending, {"gate": "gate", END: END})
    return builder
