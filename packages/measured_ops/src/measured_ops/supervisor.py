"""The supervisor graph (C2, ADR-0016; eval node C7, ADR-0018; revisions C8, ADR-0019;
execute C5, ADR-0022).

    START ─▶ call_agent ─▶ evaluate ─▶ triage ─┬─▶ execute ⟲ (one ready action per step)
                ▲                              ├─▶ gate ───┬─▶ execute (just approved)
                │                              │           ├─▶ gate     (more pending)
                │                              │           ├─▶ revise ─┐ (rejected with
                └──────────────────────────────┼───────────┼───────────┘  feedback, < max)
                                               └───────────┴─▶ END
    execute routes like gate: next unexecuted ready action, else gate, revise or END.

- `call_agent` runs the task through the injected agent's subgraph (`run_agent`)
  and appends its `AgentResult`, plus the run's token usage per model. Agents are
  passed in, never imported (ADR-0014). Each run is named `agent:<name>` for tracing.
- `evaluate` scores every proposed action with `measured_evals` and writes the scored
  copies to `evaluated_actions`. It is the only writer of `eval_scores` (ADR-0008).
  The scoring context is the supervisor's (`eval_context`), never the agent's.
- `triage` classifies every scored action with the policy, once: "auto" → `ready`,
  "never" → `blocked` (logged by the policy), "approval" → `pending_actions`. It
  runs before the gate because `policy.decide` logs, and the gate re-runs on resume.
- `gate` pauses on the head of `pending_actions` with `interrupt()`. On resume it
  checks the `ApprovalDecision` (right action, never unblocks a "never") inside
  `interrupt()` itself, then moves the action to `ready` or records the rejection.
  Nothing before the `interrupt()` call has side effects.

- `revise` (C8, ADR-0019): when every action of the round is decided and one was
  rejected **with feedback**, it builds the next `Task` for the same agent: `parent_id`
  = the task it revises, the feedback, and `inputs` + `previous_preview` (the rejected
  preview) + `revision` (the task's number + 1). The cap reads that number, never the
  count of results (a thread may hold several tasks once C6 creates them). No
  interrupt and no I/O, so it runs once per pass; the gate is unchanged.

- `execute` (C5, ADR-0022) runs the first `ready` action without an outcome through
  `tools.executors.execute_action` and appends its `ActionOutcome` to `executed`: one action
  per step, so each side effect is checkpointed before the next starts. Types with no
  executor get a "manual" outcome (`approve_draft`, ADR-0019); an executable type without
  configured tools, or a failed tool call (after the RetryPolicy for transient errors),
  raises and stops the run. Executors read before they write, so re-running the step
  after a crash finds the change in place. `evaluate` also re-checks every executable
  action (params, target, preview, scope) before anything is scored or decided.

- Before any agent runs (C10, ADR-0023): the kill switch (`killswitch.KillSwitch`, on by
  default from the environment) and the run's budget (`budget.Budget`, optional). Both
  raise before money is spent; the budget is also checked before every model call inside
  the agent (`budget.guard_budget`).

A rejection without feedback, or past the cap, ends the run with the decision recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
from contextlib import nullcontext
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt
from pydantic import AfterValidator

from measured_evals import EvalScores, evaluate
from measured_evals.budget import Budget, check_budget
from measured_evals.checks import TARGET_CHECKS
from measured_evals.cost import Prices
from measured_ops.agent import (
    Agent,
    AgentContractError,
    AgentGraph,
    agent_run_config,
    check_agent_graph,
    run_agent,
)
from measured_ops.budget import guard_budget
from measured_ops.contracts import ApprovalDecision, OpsState, PendingAction, Task
from measured_ops.killswitch import KillSwitch
from measured_ops.policy import ApprovalPolicy
from measured_ops.prices import load_model_prices
from measured_ops.tools.errors import ScopeError, ToolConfigError, ToolUnavailableError
from measured_ops.tools.executors import Executors, execute_action
from measured_ops.tools.specs import SPECS
from measured_ops.usage import collect_usage

log = logging.getLogger("measured_ops.supervisor")

SupervisorGraph = StateGraph[OpsState, None, OpsState, OpsState]
Evaluator = Callable[[str, Mapping[str, object]], Awaitable[EvalScores]]


# Transient tool failures (unreachable, timed out) retry the execute step; executors
# read before they write, so a retry never applies a change twice.
EXECUTE_RETRY = RetryPolicy(max_attempts=3, retry_on=ToolUnavailableError)


def unexecuted(state: OpsState) -> list[PendingAction]:
    """Ready actions without an outcome yet, in the order they became ready."""
    done = {o.action_id for o in state.get("executed") or []}
    return [a for a in state.get("ready") or [] if a.id not in done]


def route_pending(state: OpsState) -> Literal["execute", "gate", "__end__"]:
    """Pure router after triage: run what's ready, then the gate while anything waits."""
    if unexecuted(state):
        return "execute"
    return "gate" if state.get("pending_actions") else "__end__"


DEFAULT_MAX_REVISIONS = 3
FEEDBACK_MAX = 4000  # Task.feedback's limit
PREVIEW_SEPARATOR = "\n\n---\n\n"


def task_revision(task: Task) -> int | None:
    """The task's revision number (0 for a first task), or None if it's not a valid one."""
    value = task.inputs.get("revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def rejected_with_feedback(state: OpsState) -> list[tuple[PendingAction, str]]:
    """This round's actions that a human rejected with feedback, in decision order."""
    round_actions = {a.id: a for a in state.get("evaluated_actions") or []}
    return [
        (round_actions[d.action_id], d.feedback)
        for d in state.get("decisions") or []
        if not d.approved and d.feedback and d.action_id in round_actions
    ]


def route_after_gate(
    state: OpsState, max_revisions: int
) -> Literal["execute", "gate", "revise", "__end__"]:
    """Pure router after gate and execute: run what's ready (an action just approved);
    next pending action; else a revision if earned and allowed; else END."""
    if unexecuted(state):
        return "execute"
    if state.get("pending_actions"):
        return "gate"
    if not rejected_with_feedback(state):
        return "__end__"
    revision = task_revision(state["task"])
    if revision is None:
        log.warning(
            "supervisor: task %s has an invalid revision number; not revising",
            state["task"].id,
        )
        return "__end__"
    return "revise" if revision < max_revisions else "__end__"


def revision_task(state: OpsState) -> Task:
    """The next Task for a round rejected with feedback (ADR-0019)."""
    task = state["task"]
    rejected = rejected_with_feedback(state)
    feedback = "\n\n".join(f for _, f in rejected)
    if len(feedback) > FEEDBACK_MAX:
        feedback = feedback[: FEEDBACK_MAX - 1] + "…"
    previous = PREVIEW_SEPARATOR.join(a.preview for a, _ in rejected)
    revision = task_revision(task)
    assert revision is not None  # route_after_gate checked it
    return Task(
        agent=task.agent,
        goal=task.goal,
        inputs={**task.inputs, "previous_preview": previous, "revision": revision + 1},
        feedback=feedback,
        parent_id=task.id,
    )


def eval_payload(scores: EvalScores | None) -> dict[str, Any] | None:
    """JSON-safe eval block: names, scores and short reasons, never matched text."""
    if scores is None:
        return None
    return {
        "evaluator": scores.evaluator,
        "passed": scores.passed,
        "failing": list(scores.failing),
        "scores": dict(scores.scores),
        "reasons": dict(scores.reasons),
    }


def _merge(preview: EvalScores, target: EvalScores) -> EvalScores:
    """One EvalScores for an action: the preview's metrics plus `target_`-prefixed ones."""
    return EvalScores(
        {**preview.scores, **{f"target_{k}": v for k, v in target.scores.items()}},
        threshold=preview.threshold,
        evaluator=preview.evaluator,
        reasons={**preview.reasons, **{f"target_{k}": v for k, v in target.reasons.items()}},
    )


def gate_payload(action: PendingAction) -> dict[str, Any]:
    """What the human (CLI, inbox) sees: JSON-safe, with the eval node's scores (C7)."""
    return {
        "action_id": action.id,
        "task_id": action.task_id,
        "type": action.type,
        "target": action.target,
        "preview": action.preview,
        "risk_level": action.risk_level,
        "eval": eval_payload(action.eval_scores),
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


def build_supervisor(
    agents: Sequence[Agent],
    policy: ApprovalPolicy,
    *,
    eval_context: Mapping[str, object] | None = None,
    evaluator: Evaluator = evaluate,
    target_evaluator: Evaluator | None = None,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
    executors: Executors | None = None,
    budget: Budget | None = None,
    prices: Prices | None = None,
    kill_switch: KillSwitch | None = None,
) -> SupervisorGraph:
    """Wire the supervisor. Compiles and checks every agent's graph now, so a broken
    agent fails at startup, not mid-run. Compile the result with a checkpointer from
    `measured_ops.checkpoint.open_checkpointer` (the gate needs one to resume).

    `eval_context` goes to every eval (e.g. `{"pii_allow": [...]}` from private
    config); `evaluator` scores each action's preview and defaults to
    `measured_evals.evaluate` (guard-v0). `target_evaluator` scores its target (a URL,
    a repo) with the injection and PII checks only; its metrics are prefixed `target_`.

    `executors` (C5, ADR-0022; `tools.mcp.build_executors`) run executable actions once
    they're ready. Without them an agent can't propose an executable type: `evaluate`
    fails loudly instead of letting an approval go nowhere.

    `budget` (C10, ADR-0023) caps each run's calls, tokens and cost, priced with `prices`
    (default: the bundled demo table); no budget, no caps. `kill_switch` defaults to
    `KillSwitch.from_env()`: every supervisor honours it."""
    context: Mapping[str, object] = dict(eval_context or {})
    switch = kill_switch or KillSwitch.from_env()
    run_prices: Prices = load_model_prices() if prices is None and budget else (prices or {})

    async def _default_target_evaluator(text: str, ctx: Mapping[str, object]) -> EvalScores:
        return await evaluate(text, ctx, checks=TARGET_CHECKS)

    score_target: Evaluator = target_evaluator or _default_target_evaluator
    graphs: dict[str, tuple[Agent, AgentGraph]] = {}
    for agent in agents:
        if agent.name in graphs:
            raise AgentContractError(f"duplicate agent name {agent.name!r}")
        graph = agent.build()
        check_agent_graph(graph)
        # Agent runs never checkpoint (C8 review): their private state types aren't in
        # OpsState's serde allowlist, so a run cut off mid-agent restarts from the start
        # of call_agent instead of resuming from a checkpoint it can't load.
        graph.checkpointer = False
        graphs[agent.name] = (agent, graph)

    async def call_agent(state: OpsState) -> dict[str, Any]:
        task = state["task"]
        if task.agent not in graphs:
            raise AgentContractError(
                f"no agent named {task.agent!r} for task {task.id}; known agents: {sorted(graphs)}"
            )
        agent, graph = graphs[task.agent]
        switch.check()  # KillSwitchEngaged: nothing runs, the run stays resumable
        prior = state.get("usage") or []
        if budget is not None:
            check_budget(budget, prior, run_prices)  # BudgetExceeded before the agent runs
        with collect_usage(agent.name) as usage:
            guard = guard_budget(budget, run_prices, prior, usage) if budget else nullcontext()
            try:
                with guard:  # and before every model call inside the agent
                    result = await run_agent(
                        agent, graph, task, config=agent_run_config(agent.name, task)
                    )
            except BaseException:  # incl. cancellation (an inbox revision's timeout)
                # the run's spend is lost with the state update: keep it in the log
                per_model: dict[str, int] = {}
                for u in usage.calls():
                    per_model[u.model] = per_model.get(u.model, 0) + u.tokens
                spent = ", ".join(f"{m}: {t} tokens" for m, t in per_model.items())
                log.warning(
                    "supervisor: agent %r failed after using %s",
                    agent.name,
                    spent or "no model calls",
                )
                raise
        log.info(
            "supervisor: task %s → agent %r %s with %d action(s)",
            task.id,
            agent.name,
            result.status,
            len(result.actions),
        )
        return {"results": [result], "usage": usage.calls()}

    async def evaluate_actions(state: OpsState) -> dict[str, Any]:
        decided = {d.action_id for d in state.get("decisions") or []}
        reused = sorted(a.id for a in state["results"][-1].actions if a.id in decided)
        if reused:
            # Revisions key on action ids: a reused id could match an earlier round's
            # rejection and revise again (C8 review). Ids must be new every round.
            raise AgentContractError(
                f"agent {state['task'].agent!r} reused already-decided action ids {reused}"
            )
        for action in state["results"][-1].actions:
            check_executable(action)
        scored: list[PendingAction] = []
        for action in state["results"][-1].actions:
            scores = _merge(
                await evaluator(action.preview, context),
                await score_target(action.target, context),
            )
            # metric names only in the log: reasons and values stay out (ADR-0018)
            log.info(
                "eval: action %s (%s) %s",
                action.id,
                action.type,
                "passed" if scores.passed else f"failed {list(scores.failing) or ['(no scores)']}",
            )
            scored.append(action.model_copy(update={"eval_scores": scores}))
        return {"evaluated_actions": scored}

    def check_executable(action: PendingAction) -> None:
        """An executable action must render to what it runs, in scope, with an executor."""
        if action.type not in SPECS:
            return
        if executors is None:
            raise AgentContractError(
                f"action {action.id} is {action.type}, which runs through an executor, and "
                "this supervisor has no tools configured"
            )
        try:
            executors.check(action)  # AgentContractError on a params/preview mismatch
        except (ScopeError, ToolConfigError) as exc:
            raise AgentContractError(f"action {action.id} ({action.type}): {exc}") from exc

    async def triage(state: OpsState) -> dict[str, Any]:
        ready: list[PendingAction] = []
        blocked: list[PendingAction] = []
        pending: list[PendingAction] = []
        for action in state["evaluated_actions"]:
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

    async def execute(state: OpsState) -> dict[str, Any]:
        # One action per step: its outcome is checkpointed before the next one runs.
        action = unexecuted(state)[0]
        outcome = await execute_action(action, executors, state.get("executed") or [])
        return {"executed": [outcome]}

    async def revise(state: OpsState) -> dict[str, Any]:
        # No interrupt, no I/O: runs once per pass (ADR-0019).
        task = revision_task(state)
        log.info(
            "supervisor: task %s rejected with feedback; revision %s as task %s",
            task.parent_id,
            task.inputs["revision"],
            task.id,
        )
        return {"task": task}

    if max_revisions < 0:
        raise ValueError(f"max_revisions must be >= 0, got {max_revisions}")
    builder: SupervisorGraph = StateGraph(OpsState)
    builder.add_node("call_agent", call_agent)
    builder.add_node("evaluate", evaluate_actions)
    builder.add_node("triage", triage)
    builder.add_node("gate", gate)
    builder.add_node("execute", execute, retry_policy=EXECUTE_RETRY)
    builder.add_node("revise", revise)
    builder.add_edge(START, "call_agent")
    builder.add_edge("call_agent", "evaluate")
    builder.add_edge("evaluate", "triage")
    builder.add_conditional_edges(
        "triage", route_pending, {"execute": "execute", "gate": "gate", END: END}
    )
    after: dict[Hashable, str] = {
        "execute": "execute",
        "gate": "gate",
        "revise": "revise",
        END: END,
    }
    for node in ("gate", "execute"):
        builder.add_conditional_edges(
            node, lambda state: route_after_gate(state, max_revisions), after
        )
    builder.add_edge("revise", "call_agent")
    return builder
