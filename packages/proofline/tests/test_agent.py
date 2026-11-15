"""The content agent (C8, ADR-0019): the pipeline as a Measured Ops agent.

Fake LLM and the stub researcher: no network, no keys. Runs through the real
supervisor with the demo policy and an in-memory checkpointer, strict msgpack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from measured_ops import ApprovalDecision, Task, initial_ops_state, load_policy
from measured_ops.agent import check_agent_graph
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.supervisor import build_supervisor
from proofline.agent import APPROVE_DRAFT, ContentAgent
from tests.fakes import FakeLLM, make_critique, make_deps, make_draft, make_outline

pytestmark = pytest.mark.anyio

TOPIC = "Why every AI loop needs an exit"


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _agent(tmp_path: Path, *critic_scores: int) -> ContentAgent:
    llm = FakeLLM().queue(make_outline())
    for score in critic_scores or (9,):
        llm.queue(make_draft(), make_critique(score))
    return ContentAgent(make_deps(tmp_path, llm))


def _app(agent: ContentAgent) -> Any:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    return build_supervisor([agent], load_policy()).compile(checkpointer=saver)


def _task(**inputs: Any) -> Task:
    return Task(
        agent="content",
        goal="Draft a post and a clip script.",
        inputs={"topic": TOPIC, "audience": "developers new to AI", **inputs},
    )


CFG: Any = {"configurable": {"thread_id": "c1"}, "recursion_limit": 40}


def test_content_agent_meets_the_agent_contract(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    assert agent.name == "content"
    check_agent_graph(agent.build())


async def test_one_topic_pauses_at_the_gate_with_one_approve_draft(tmp_path: Path) -> None:
    out = await _app(_agent(tmp_path)).ainvoke(initial_ops_state(_task()), CFG)

    [intr] = out["__interrupt__"]
    assert intr.value["type"] == APPROVE_DRAFT == "approve_draft"
    assert intr.value["target"] == "manual paste: linkedin"
    preview = intr.value["preview"]
    for heading in ("## LinkedIn post", "## Clip script", "## Lesson outline"):
        assert heading in preview
    assert intr.value["eval"] is not None  # scored by the eval node, not the agent
    [result] = out["results"]
    assert result.status == "succeeded"
    assert [a.eval_scores for a in result.actions] == [None]


async def test_the_critic_loop_runs_inside_the_agent(tmp_path: Path) -> None:
    agent = _agent(tmp_path, 6, 9)  # first draft below threshold, second passes
    out = await _app(agent).ainvoke(initial_ops_state(_task()), CFG)

    assert "critic 9.0 after 1 revision" in out["results"][0].summary
    roles = [c.role for c in agent.deps.llm.calls]  # type: ignore[attr-defined]
    assert roles == ["planner", "writer", "critic", "writer", "critic"]


async def test_approve_draft_never_publishes_approval_only_marks_it_ready(
    tmp_path: Path,
) -> None:
    # ADR-0019: approving moves the action to `ready` and nothing else. No executor is
    # ever registered for approve_draft; Hassan pastes the post himself.
    assert load_policy().decide_type(APPROVE_DRAFT) == "approval"
    app = _app(_agent(tmp_path))
    out = await app.ainvoke(initial_ops_state(_task()), CFG)
    action_id = out["__interrupt__"][0].value["action_id"]

    decision = ApprovalDecision(action_id=action_id, approved=True)
    done = await app.ainvoke(Command(resume=decision.model_dump()), CFG)

    assert [a.type for a in done["ready"]] == [APPROVE_DRAFT]
    assert done["blocked"] == []
    assert done["pending_actions"] == []
    assert len(done["results"]) == 1  # approving never runs the agent again


@pytest.mark.parametrize(
    "inputs",
    [{"topic": ""}, {"topic": 3}, {"audience": None}],
)
async def test_bad_inputs_fail_the_result_with_a_reason(
    tmp_path: Path, inputs: dict[str, Any]
) -> None:
    out = await _app(_agent(tmp_path)).ainvoke(initial_ops_state(_task(**inputs)), CFG)

    [result] = out["results"]
    assert result.status == "failed"
    assert result.error is not None
    assert "topic" in result.error or "audience" in result.error
    assert out["ready"] == out["pending_actions"] == []


async def test_a_revision_task_brings_feedback_and_the_previous_preview_to_the_writer(
    tmp_path: Path,
) -> None:
    agent = _agent(tmp_path)
    first = _task()
    revision = Task(
        agent="content",
        goal=first.goal,
        inputs={**first.inputs, "previous_preview": "## LinkedIn post\n\nOld hook.", "revision": 1},
        feedback="Make the hook concrete.",
        parent_id=first.id,
    )

    await _app(agent).ainvoke(initial_ops_state(revision), CFG)

    writer_prompt = next(c for c in agent.deps.llm.calls if c.role == "writer").prompt.user  # type: ignore[attr-defined]
    assert "Old hook." in writer_prompt
    assert "Make the hook concrete." in writer_prompt


async def test_previous_preview_must_be_text(tmp_path: Path) -> None:
    out = await _app(_agent(tmp_path)).ainvoke(
        initial_ops_state(_task(previous_preview=["not", "text"])), CFG
    )
    [result] = out["results"]
    assert result.status == "failed"
    assert "previous_preview" in (result.error or "")


async def test_the_agent_logs_critic_scores_by_action_id_and_nothing_else(
    tmp_path: Path,
) -> None:
    import json

    from measured_ops.judge_log import JsonlJudgeLog

    path = tmp_path / "judge.jsonl"
    llm = FakeLLM().queue(make_outline(), make_draft(), make_critique(9))
    agent = ContentAgent(make_deps(tmp_path, llm), judge_log=JsonlJudgeLog(path))

    out = await _app(agent).ainvoke(initial_ops_state(_task()), CFG)

    [line] = [json.loads(x) for x in path.read_text().splitlines()]
    assert set(line) == {"kind", "action_id", "critic"}
    assert line["action_id"] == out["__interrupt__"][0].value["action_id"]
    assert line["critic"]["overall"] == make_critique(9).overall
    assert len(line["critic"]) == len(make_critique(9).scores) + 1
    raw = path.read_text()
    assert make_draft().title not in raw
    assert "linkedin" not in raw
