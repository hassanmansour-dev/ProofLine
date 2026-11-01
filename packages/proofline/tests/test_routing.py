from __future__ import annotations

from typing import Any

import pytest

from proofline.routing import route_after_critic, route_after_human
from proofline.schemas import Critique
from proofline.state import ScriptState
from tests.fakes import make_critique, make_settings


def _state(critique: Critique, revision_count: int, tmp_path: Any, **cfg: Any) -> ScriptState:
    settings = make_settings(tmp_path, **cfg)
    return ScriptState(
        critique=critique,
        score=critique.overall,
        revision_count=revision_count,
        run_config=settings.run_config(),
    )


@pytest.mark.parametrize(
    ("critique", "revisions", "expected"),
    [
        pytest.param(make_critique(9), 0, "human_review", id="above-threshold"),
        pytest.param(make_critique(8), 0, "human_review", id="exactly-threshold"),
        pytest.param(make_critique(7), 0, "writer", id="below-threshold-budget-left"),
        pytest.param(make_critique(7), 2, "writer", id="below-threshold-last-revision-left"),
        pytest.param(make_critique(7), 3, "human_review", id="budget-exhausted"),
        pytest.param(
            make_critique(10, technical_accuracy=5), 0, "writer", id="accuracy-gate-forces-revision"
        ),
    ],
)
def test_route_after_critic(
    critique: Critique, revisions: int, expected: str, tmp_path: Any
) -> None:
    assert route_after_critic(_state(critique, revisions, tmp_path)) == expected


def test_route_after_critic_respects_snapshot_not_live_settings(tmp_path: Any) -> None:
    state = _state(make_critique(7), 0, tmp_path, score_threshold=7.0)
    assert route_after_critic(state) == "human_review"


@pytest.mark.parametrize(
    ("approved", "expected"),
    [(True, "finalizer"), (False, "writer"), (None, "writer")],
)
def test_route_after_human(approved: bool | None, expected: str) -> None:
    state = ScriptState() if approved is None else ScriptState(approved=approved)
    assert route_after_human(state) == expected
