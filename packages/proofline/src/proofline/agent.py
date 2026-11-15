"""The content agent (C8, ADR-0019): the pipeline as a Measured Ops agent.

    Task(agent="content", inputs={topic, audience}) ──▶ planner → researcher →
    writer ⇄ critic → finalizer ──▶ AgentResult(actions=[approve_draft])

One action per topic, `approve_draft`, whose preview holds the LinkedIn post, the
1-minute clip script and the lesson outline (`render.approve_draft_preview`). It
**never publishes**: approving it only moves it to `ready`; no executor is ever
registered for this type, and Hassan pastes the post himself (ADR-0019).

The critic's score is this agent's own quality loop. The supervisor's eval node writes
`eval_scores` (ADR-0008); this agent never does.

Dependencies come through the constructor (`Deps`: settings, LLM, researcher), so the
supervisor needs nothing agent-specific. The pipeline runs nested in the supervisor's
thread: it inherits the thread id (outputs are keyed by it) and the supervisor's
callbacks, including the masked tracer (ADR-0018).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from measured_ops import AgentResult, PendingAction, Task
from measured_ops.agent import AgentGraph, AgentInput, AgentOutput
from measured_ops.judge_log import JudgeLog, NullJudgeLog
from proofline.context import Deps
from proofline.graph import build_graph
from proofline.render import approve_draft_preview
from proofline.state import initial_state

log = logging.getLogger("proofline.agent")

AGENT_NAME = "content"
APPROVE_DRAFT = "approve_draft"
TARGET = "manual paste: linkedin"


class _State(TypedDict, total=False):
    task: Task
    result: AgentResult


def _optional_text_input(task: Task, key: str) -> str | None:
    value = task.inputs.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"task input {key!r} must be a string")
    return value


def _text_input(task: Task, key: str) -> str:
    value = task.inputs.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task input {key!r} must be a non-empty string")
    return value.strip()


class ContentAgent:
    """Conforms to `measured_ops.Agent`."""

    name = AGENT_NAME

    def __init__(self, deps: Deps, judge_log: JudgeLog | None = None) -> None:
        self.deps = deps
        self.judge_log: JudgeLog = NullJudgeLog() if judge_log is None else judge_log

    def build(self) -> AgentGraph:
        deps = self.deps
        judge_log = self.judge_log
        # No checkpoints of its own: its state types aren't in the supervisor's serde
        # allowlist, so a cut-off run must restart, not resume from an unreadable
        # checkpoint. It still gets the thread id and callbacks from the supervisor.
        pipeline = build_graph().compile(checkpointer=False)

        async def draft(state: _State) -> dict[str, Any]:
            task = state["task"]
            try:
                topic = _text_input(task, "topic")
                audience = _text_input(task, "audience")
                previous_preview = _optional_text_input(task, "previous_preview")
            except ValueError as exc:
                return {"result": _failed(task, str(exc))}

            settings = deps.settings
            inputs = initial_state(
                topic,
                audience,
                settings.run_config(),
                feedback=task.feedback,
                previous_preview=previous_preview,
            )
            # No `configurable` and no `callbacks`: the thread id and the masked tracer
            # come from the supervisor's run.
            cfg = RunnableConfig(
                run_name="content-pipeline",
                recursion_limit=settings.recursion_limit,
                metadata={"task_id": task.id},
            )
            out = await pipeline.ainvoke(inputs, cfg, context=deps)

            final = out["final_script"]
            action = PendingAction(
                task_id=task.id,
                type=APPROVE_DRAFT,
                target=TARGET,
                preview=approve_draft_preview(out["draft"], out["research_notes"], out["outline"]),
                risk_level="medium",
            )
            revisions = final.revision_count
            score = (
                "no critic score"
                if final.final_score is None
                else f"critic {final.final_score:.1f}"
            )
            summary = (
                f"Drafted “{final.title}”: {final.word_count} words, {score} after "
                f"{revisions} revision{'' if revisions == 1 else 's'}."
            )
            log.info("content: task %s → action %s (%s)", task.id, action.id, score)
            critique = out.get("critique")
            if critique is not None:  # ids and scores only, never the draft (ADR-0019)
                scores: dict[str, float] = {c.criterion: c.score for c in critique.scores}
                judge_log.record_critic(action.id, {**scores, "overall": critique.overall})
            return {
                "result": AgentResult(
                    task_id=task.id,
                    agent=AGENT_NAME,
                    status="succeeded",
                    summary=summary,
                    actions=(action,),
                )
            }

        builder = StateGraph(_State, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("draft", draft)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", END)
        return builder.compile()


def _failed(task: Task, error: str) -> AgentResult:
    return AgentResult(task_id=task.id, agent=AGENT_NAME, status="failed", summary="", error=error)
