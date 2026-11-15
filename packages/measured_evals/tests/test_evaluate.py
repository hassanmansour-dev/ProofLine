"""The eval interface: generic arguments, scores in [0, 1], fails closed (C1; checks in C7)."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from measured_evals import EvalScores, evaluate


def test_unknown_context_keys_are_ignored() -> None:
    assert asyncio.run(evaluate("A plain sentence.", {"task": "anything"})).passed is True


def test_no_checks_means_not_passed() -> None:
    result = asyncio.run(evaluate("text", checks=()))
    assert result.scores == {}
    assert result.passed is False


def test_reasons_need_a_matching_score() -> None:
    with pytest.raises(ValueError, match="no score"):
        EvalScores({"quality": 1.0}, reasons={"pii": "email found"})


@pytest.mark.parametrize("bad", ["", "x" * 201])
def test_reasons_are_short_text(bad: str) -> None:
    with pytest.raises(ValueError, match="characters"):
        EvalScores({"pii": 0.0}, reasons={"pii": bad})


def test_passed_needs_every_score_at_or_above_threshold() -> None:
    assert EvalScores({"quality": 0.7, "safety": 0.9}, threshold=0.7).passed is True
    below = EvalScores({"quality": 0.69, "safety": 0.9}, threshold=0.7)
    assert below.passed is False
    assert below.failing == ("quality",)


def test_no_scores_means_not_passed() -> None:
    assert EvalScores({}, threshold=0.0).passed is False


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_scores_must_be_in_unit_range(bad: float) -> None:
    with pytest.raises(ValueError, match="must be in"):
        EvalScores({"quality": bad})


@pytest.mark.parametrize("bad", [True, "0.9", None])
def test_scores_must_be_numbers(bad: object) -> None:
    with pytest.raises(ValueError, match="must be a number"):
        EvalScores({"quality": bad})  # type: ignore[dict-item]


def test_threshold_must_be_in_unit_range() -> None:
    with pytest.raises(ValueError, match="threshold"):
        EvalScores({"quality": 0.5}, threshold=2.0)


def test_empty_metric_name_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        EvalScores({"": 0.5})


def test_frozen_and_isolated_from_caller_dict() -> None:
    raw = {"quality": 0.9}
    result = EvalScores(raw)
    raw["quality"] = 0.1
    assert result.scores["quality"] == 0.9
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.threshold = 0.1  # type: ignore[misc]
