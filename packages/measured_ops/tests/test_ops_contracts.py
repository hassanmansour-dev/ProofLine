"""C1 contract models: Task, PendingAction, AgentResult, ApprovalDecision, OpsState."""

from __future__ import annotations

import typing
from typing import Any

import pytest
from ops_fakes import make_action, make_task
from pydantic import BaseModel, ValidationError

from measured_evals import EvalScores
from measured_ops import AgentResult, ApprovalDecision, OpsState, Task
from measured_ops.contracts import SERDE_ALLOWLIST, STATE_TYPES, initial_ops_state

# ── Task ──────────────────────────────────────────────────────────────────────


def test_task_defaults() -> None:
    task = make_task()
    assert task.id
    assert task.inputs == {}
    assert task.feedback is None
    assert task.parent_id is None


def test_task_ids_are_unique() -> None:
    assert make_task().id != make_task().id


@pytest.mark.parametrize("agent", ["Content", "content-agent", "", "1st"])
def test_agent_name_is_snake_case(agent: str) -> None:
    with pytest.raises(ValidationError):
        make_task(agent=agent)


def test_revision_task_needs_a_parent() -> None:
    with pytest.raises(ValidationError, match="parent_id"):
        make_task(feedback="Open with the crash story.")
    parent = make_task()
    revision = make_task(feedback="Open with the crash story.", parent_id=parent.id)
    assert revision.parent_id == parent.id


def test_task_cannot_revise_itself() -> None:
    with pytest.raises(ValidationError, match="itself"):
        make_task(id="t1", parent_id="t1")


def test_task_rejects_unknown_fields_and_is_frozen() -> None:
    with pytest.raises(ValidationError):
        Task(agent="echo", goal="x", priority=1)  # type: ignore[call-arg]
    task = make_task()
    with pytest.raises(ValidationError):
        task.goal = "changed"  # type: ignore[misc]


def test_task_inputs_must_be_json() -> None:
    assert make_task(inputs={"topic": "evals", "n": 2, "tags": ["a"]}).inputs["n"] == 2
    with pytest.raises(ValidationError):
        make_task(inputs={"when": object()})


# ── PendingAction ─────────────────────────────────────────────────────────────


def test_pending_action_carries_the_five_contract_fields() -> None:
    action = make_action("t1", eval_scores=EvalScores({"quality": 0.8}))
    assert (action.type, action.target, action.preview, action.risk_level) == (
        "publish_public",
        "linkedin",
        "A draft post.",
        "medium",
    )
    assert isinstance(action.eval_scores, EvalScores)


def test_eval_scores_are_empty_until_the_eval_node() -> None:
    assert make_action("t1").eval_scores is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("type", "Publish Post"), ("risk_level", "critical"), ("preview", ""), ("target", "")],
)
def test_pending_action_validation(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        make_action("t1", **{field: value})


def test_pending_action_rejects_out_of_range_eval_scores() -> None:
    with pytest.raises(ValidationError):
        make_action("t1", eval_scores={"scores": {"quality": 3.0}})


# ── AgentResult ───────────────────────────────────────────────────────────────


def _result(**overrides: Any) -> AgentResult:
    fields: dict[str, Any] = {
        "task_id": "t1",
        "agent": "echo",
        "status": "succeeded",
        "summary": "Done.",
    }
    fields.update(overrides)
    return AgentResult(**fields)


def test_succeeded_result_with_actions() -> None:
    result = _result(actions=(make_action("t1"), make_action("t1")))
    assert len(result.actions) == 2


def test_failed_result_needs_an_error() -> None:
    with pytest.raises(ValidationError, match="error"):
        _result(status="failed")
    assert _result(status="failed", error="search timed out").error == "search timed out"


def test_succeeded_result_has_no_error() -> None:
    with pytest.raises(ValidationError, match="no `error`"):
        _result(error="oops")


def test_failed_result_proposes_no_actions() -> None:
    with pytest.raises(ValidationError, match="no actions"):
        _result(status="failed", error="x", actions=(make_action("t1"),))


def test_actions_must_belong_to_the_result_task() -> None:
    with pytest.raises(ValidationError, match="other tasks"):
        _result(actions=(make_action("t2"),))


def test_action_ids_unique_within_a_result() -> None:
    action = make_action("t1")
    with pytest.raises(ValidationError, match="unique"):
        _result(actions=(action, action))


# ── ApprovalDecision ──────────────────────────────────────────────────────────


def test_approval_decision_feedback_is_optional() -> None:
    assert ApprovalDecision(action_id="a1", approved=False).feedback is None
    assert ApprovalDecision(action_id="a1", approved=True).approved is True


# ── Round trips and OpsState ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model",
    [
        make_task(inputs={"topic": "evals"}),
        make_action("t1"),
        _result(actions=(make_action("t1"),)),
        ApprovalDecision(action_id="a1", approved=False, feedback="Shorter."),
    ],
    ids=["task", "action", "result", "decision"],
)
def test_models_round_trip_json(model: BaseModel) -> None:
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_initial_ops_state_sets_every_field() -> None:
    task = make_task()
    state = initial_ops_state(task)
    assert set(state) == set(typing.get_type_hints(OpsState))
    assert state["task"] is task
    assert state["results"] == state["pending_actions"] == state["decisions"] == []


def test_accumulating_fields_have_reducers() -> None:
    hints = typing.get_type_hints(OpsState, include_extras=True)
    for field in ("results", "decisions"):
        assert typing.get_origin(hints[field]) is typing.Annotated, field


def _types_in(tp: Any) -> set[type]:
    found: set[type] = set()
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        found.add(tp)
        for f in tp.model_fields.values():
            found |= _types_in(f.annotation)
    elif tp is EvalScores:
        found.add(tp)
    for arg in typing.get_args(tp):
        found |= _types_in(arg)
    return found


def test_every_state_type_is_allowlisted() -> None:
    reachable: set[type] = set()
    for tp in typing.get_type_hints(OpsState, include_extras=True).values():
        reachable |= _types_in(tp)
    assert reachable == set(STATE_TYPES)
    assert {(t.__module__, t.__qualname__) for t in reachable} == set(SERDE_ALLOWLIST)
