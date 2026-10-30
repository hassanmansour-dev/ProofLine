from __future__ import annotations

import pytest
from pydantic import ValidationError

from proofline.schemas import Critique, Draft, RunView, Source
from tests.fakes import make_critique, make_draft


def test_overall_is_weighted_mean() -> None:
    # hook and clarity weigh 1.5, accuracy 2.0; all others 1.0 → total weight 10
    c = make_critique(8, hook=10, clarity=10)
    assert c.overall == pytest.approx((10 * 1.5 + 10 * 1.5 + 8 * 7) / 10, abs=0.05)


def test_accuracy_gate_caps_overall() -> None:
    c = make_critique(10, technical_accuracy=5)
    assert c.overall == 6.0


def test_accuracy_at_gate_is_not_capped() -> None:
    c = make_critique(10, technical_accuracy=6)
    assert c.overall > 6.0


def test_critique_requires_all_criteria() -> None:
    full = make_critique(8)
    with pytest.raises(ValidationError):
        Critique(scores=full.scores[:-1], must_fix=[])


def test_word_count_ignores_citations() -> None:
    d = make_draft()
    assert "[S1]" in d.spoken_text
    assert d.word_count == len(
        d.spoken_text.replace("[S1]", "").replace("[S2]", "").replace("[S3]", "").split()
    )
    assert d.estimated_seconds == round(d.word_count / 150 * 60)


def test_source_id_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        Source(id="source-1", url="https://x", title="x", content="x")


@pytest.mark.parametrize("model", [make_draft(), make_critique(7)], ids=["draft", "critique"])
def test_models_round_trip_json(model: Draft | Critique) -> None:
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_run_view_round_trip() -> None:
    view = RunView(
        thread_id="t",
        status="awaiting_review",
        draft=make_draft(),
        critique=make_critique(7),
        score=7.0,
    )
    assert RunView.model_validate_json(view.model_dump_json()) == view
