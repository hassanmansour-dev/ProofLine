"""The judge-agreement log (C8, ADR-0019): action id, critic scores, eval scores and the
decision. Never draft text, previews or targets. One JSON object per line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from measured_ops.judge_log import JsonlJudgeLog, NullJudgeLog, judge_log_from_env

CRITIC_KEYS = {"kind", "action_id", "critic"}
DECISION_KEYS = {"kind", "action_id", "eval", "approved"}


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_critic_and_decision_lines_hold_exactly_the_allowed_fields(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    log = JsonlJudgeLog(path)

    log.record_critic("a1", {"hook": 8, "technical_accuracy": 9, "overall": 8.4})
    log.record_decision("a1", {"quality": 1.0, "pii": 0.0}, approved=False)

    critic, decision = _lines(path)
    assert set(critic) == CRITIC_KEYS
    assert critic == {
        "kind": "critic",
        "action_id": "a1",
        "critic": {"hook": 8, "technical_accuracy": 9, "overall": 8.4},
    }
    assert set(decision) == DECISION_KEYS
    assert decision == {
        "kind": "decision",
        "action_id": "a1",
        "eval": {"quality": 1.0, "pii": 0.0},
        "approved": False,
    }


def test_a_decision_without_eval_scores_records_null(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    JsonlJudgeLog(path).record_decision("a1", None, approved=True)
    assert _lines(path) == [{"kind": "decision", "action_id": "a1", "eval": None, "approved": True}]


@pytest.mark.parametrize("bad", [{"hook": "great"}, {"": 1.0}, {"x": float("nan")}])
def test_scores_must_be_named_numbers(tmp_path: Path, bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="score"):
        JsonlJudgeLog(tmp_path / "j.jsonl").record_critic("a1", bad)  # type: ignore[arg-type]


def test_no_room_for_text_the_api_takes_only_ids_scores_and_a_bool() -> None:
    # The port's methods have no parameter that could carry a draft, preview or target.
    import inspect

    params = {
        name: list(inspect.signature(getattr(JsonlJudgeLog, name)).parameters)
        for name in ("record_critic", "record_decision")
    }
    assert params == {
        "record_critic": ["self", "action_id", "scores"],
        "record_decision": ["self", "action_id", "eval_scores", "approved"],
    }


def test_creates_the_directory_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "judge.jsonl"
    log = JsonlJudgeLog(path)
    log.record_decision("a1", None, approved=True)
    log.record_decision("a2", None, approved=False)
    assert [line["action_id"] for line in _lines(path)] == ["a1", "a2"]


def test_null_log_writes_nothing(tmp_path: Path) -> None:
    NullJudgeLog().record_critic("a1", {"hook": 1})
    NullJudgeLog().record_decision("a1", None, approved=True)
    assert list(tmp_path.iterdir()) == []


def test_path_comes_from_the_environment_default_under_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MEASURED_OPS_JUDGE_LOG_PATH", raising=False)
    default = judge_log_from_env()
    assert isinstance(default, JsonlJudgeLog)
    assert default.path == Path(".data/judge_log.jsonl")  # gitignored
    monkeypatch.setenv("MEASURED_OPS_JUDGE_LOG_PATH", str(tmp_path / "j.jsonl"))
    assert judge_log_from_env().path == tmp_path / "j.jsonl"  # type: ignore[attr-defined]


def test_the_default_path_is_gitignored() -> None:
    import subprocess

    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".data/judge_log.jsonl"], cwd=root, check=False
    )
    assert result.returncode == 0
