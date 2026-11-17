"""Critic calibration sheet and agreement metric (GROW-14).

Every score in this file is SYNTHETIC: hand-typed to exercise the arithmetic. The
outputs come from the fake writer. Nothing here measures a critic, a model or a person.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from measured_evals.__main__ import main
from measured_evals.calibration import (
    CalibrationError,
    CriticScores,
    agreement,
    agreement_lines,
    build_sheet,
    load_critic_scores,
    parse_critic_scores,
    parse_filled_sheet,
    read_outputs,
    sheet_csv,
    sheet_markdown,
)
from measured_evals.fake import grounded_writer
from measured_evals.golden import load_golden_set

SYNTHETIC_CRITIC_FILE = Path(__file__).parent / "fixtures" / "synthetic_critic_scores.json"
GOLDEN = load_golden_set()
THRESHOLD = 8.0


@pytest.fixture
def synthetic_outputs(tmp_path: Path) -> Path:
    """20 outputs from the fake writer, saved as <item-id>.md."""
    directory = tmp_path / "outputs"
    directory.mkdir()
    for item in GOLDEN.items:
        (directory / f"{item.id}.md").write_text(grounded_writer(item), encoding="utf-8")
    return directory


def _synthetic_critic(scores: dict[str, float]) -> CriticScores:
    return CriticScores(scores=scores, synthetic=True)


def _fill(sheet: str, human: dict[str, float]) -> str:
    rows = list(csv.DictReader(io.StringIO(sheet)))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "human_score": human.get(row["item_id"], "")})
    return out.getvalue()


def test_fixture_is_labelled_synthetic_and_covers_the_golden_set() -> None:
    critic = load_critic_scores(SYNTHETIC_CRITIC_FILE)
    assert critic.synthetic is True
    assert critic.source is not None
    assert critic.source.startswith("SYNTHETIC")
    assert set(critic.scores) == {item.id for item in GOLDEN.items}
    assert len(critic.scores) == 20


def test_sheet_has_a_blank_human_column_and_is_blind_by_default(synthetic_outputs: Path) -> None:
    critic = load_critic_scores(SYNTHETIC_CRITIC_FILE)
    sheet = build_sheet(read_outputs(synthetic_outputs), critic)
    rows = list(csv.DictReader(io.StringIO(sheet_csv(sheet))))
    assert len(rows) == 20
    assert [r["item_id"] for r in rows] == sorted(critic.scores)
    assert all(r["human_score"] == "" and r["notes"] == "" for r in rows)
    assert "critic_score" not in rows[0]
    assert all(int(r["words"]) > 0 for r in rows)


def test_show_critic_adds_the_column_and_a_warning(synthetic_outputs: Path) -> None:
    critic = load_critic_scores(SYNTHETIC_CRITIC_FILE)
    sheet = build_sheet(read_outputs(synthetic_outputs), critic, blind=False)
    rows = list(csv.DictReader(io.StringIO(sheet_csv(sheet))))
    assert {float(r["critic_score"]) for r in rows} == set(critic.scores.values())
    assert "anchored" in sheet_markdown(sheet)


def test_markdown_sheet_quotes_every_output_and_marks_synthetic(synthetic_outputs: Path) -> None:
    critic = load_critic_scores(SYNTHETIC_CRITIC_FILE)
    outputs = read_outputs(synthetic_outputs)
    markdown = sheet_markdown(build_sheet(outputs, critic))
    assert "SYNTHETIC" in markdown
    assert markdown.count("Your score (1 to 10): ____") == 20
    for item_id, text in outputs.items():
        assert f"`{item_id}`" in markdown
        assert f"> {text.strip().splitlines()[0]}" in markdown
    # blind: no critic score appears anywhere
    assert "Critic |" not in markdown


def test_an_output_cannot_break_out_of_its_quote() -> None:
    critic = _synthetic_critic({"a": 8.0})
    markdown = sheet_markdown(build_sheet({"a": "# Title\n\n## 2. `fake`\n| x | y |"}, critic))
    body = markdown.split("## 1. `a`")[1]
    assert "> ## 2. `fake`" in body
    assert "\n## 2. `fake`" not in body


def test_outputs_and_scores_must_cover_the_same_ids() -> None:
    critic = _synthetic_critic({"a": 8.0, "b": 7.0})
    with pytest.raises(CalibrationError, match="no output for \\['b'\\]"):
        build_sheet({"a": "text"}, critic)
    with pytest.raises(CalibrationError, match="no critic score for \\['c'\\]"):
        build_sheet({"a": "x", "b": "y", "c": "z"}, critic)
    with pytest.raises(CalibrationError, match="empty outputs"):
        build_sheet({"a": "x", "b": "  "}, critic)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"scores": {}},
        {"scores": {"a": "8"}},
        {"scores": {"a": True}},
        {"scores": {"a": 11}},
        {"scores": {"Bad Id": 8}},
        {"scores": {"a": 8}, "scale": [10, 1]},
        {"scores": {"a": 8}, "synthetic": "yes"},
        {"scores": {"a": {"hook": 8}}},
    ],
)
def test_bad_critic_scores_are_refused(document: object) -> None:
    with pytest.raises(CalibrationError):
        parse_critic_scores(document)


def test_critic_scores_accept_an_overall_object() -> None:
    critic = parse_critic_scores({"scores": {"a": {"overall": 7.5, "hook": 9}}})
    assert critic.scores == {"a": 7.5}
    assert critic.synthetic is False


def test_a_blank_cell_stops_the_computation() -> None:
    sheet = "item_id,words,human_score,notes\na,10,8,\nb,12,,\n"
    with pytest.raises(CalibrationError, match="1 of 2 rows have no human_score"):
        parse_filled_sheet(sheet)


@pytest.mark.parametrize("cell", ["high", "0", "10.5", "nan"])
def test_bad_human_scores_are_refused(cell: str) -> None:
    with pytest.raises(CalibrationError):
        parse_filled_sheet(f"item_id,words,human_score,notes\na,10,{cell},\n")


def test_duplicate_rows_and_missing_columns_are_refused() -> None:
    with pytest.raises(CalibrationError, match="twice"):
        parse_filled_sheet("item_id,human_score\na,8\na,7\n")
    with pytest.raises(CalibrationError, match="columns"):
        parse_filled_sheet("id,score\na,8\n")


def test_kappa_by_hand() -> None:
    # SYNTHETIC. 10 items: both pass 4, both fail 3, critic only 2, person only 1.
    # observed = 7/10; critic pass rate .6, person .5; expected = .6*.5 + .4*.5 = .5
    # kappa = (.7 - .5) / (1 - .5) = .4
    critic = _synthetic_critic({f"i{n}": s for n, s in enumerate([9, 9, 9, 9, 5, 5, 5, 9, 9, 5])})
    human = {f"i{n}": float(s) for n, s in enumerate([8, 8, 8, 8, 6, 6, 6, 7, 7, 8])}
    result = agreement(critic, human, threshold=THRESHOLD)
    assert (result.both_pass, result.both_fail) == (4, 3)
    assert (result.critic_only_pass, result.human_only_pass) == (2, 1)
    assert result.observed_agreement == pytest.approx(0.7)
    assert result.expected_agreement == pytest.approx(0.5)
    assert result.kappa == pytest.approx(0.4)
    # differences: 4x(+1), 3x(-1), 2x(+2), 1x(-3) -> mean 0.2, mean absolute 1.4
    assert result.mean_difference == pytest.approx(0.2)
    assert result.mean_absolute_difference == pytest.approx(1.4)
    assert result.synthetic is True


def test_perfect_agreement_is_one_and_opposite_is_minus_one() -> None:
    critic = _synthetic_critic({"a": 9.0, "b": 5.0})
    assert agreement(critic, {"a": 8.0, "b": 7.0}, threshold=THRESHOLD).kappa == pytest.approx(1.0)
    assert agreement(critic, {"a": 7.0, "b": 8.0}, threshold=THRESHOLD).kappa == pytest.approx(-1.0)


def test_kappa_is_undefined_not_invented_when_nothing_varies() -> None:
    critic = _synthetic_critic({"a": 9.0, "b": 9.5})
    result = agreement(critic, {"a": 8.0, "b": 8.5}, threshold=THRESHOLD)
    assert result.kappa is None
    assert result.kappa_undefined_because is not None
    assert result.observed_agreement == 1.0
    assert any("undefined" in line for line in agreement_lines(result))


def test_a_generous_critic_shows_in_the_difference_not_only_in_kappa() -> None:
    # SYNTHETIC. The critic is 2 points above the person on every item.
    human = {f"i{n}": float(s) for n, s in enumerate([5, 6, 7, 8])}
    critic = _synthetic_critic({k: v + 2 for k, v in human.items()})
    result = agreement(critic, human, threshold=THRESHOLD)
    assert result.mean_difference == pytest.approx(2.0)
    assert result.critic_only_pass == 2
    assert result.human_only_pass == 0


def test_agreement_needs_the_same_items_and_a_threshold_on_the_scale() -> None:
    critic = _synthetic_critic({"a": 9.0, "b": 5.0})
    with pytest.raises(CalibrationError, match="not in the sheet \\['b'\\]"):
        agreement(critic, {"a": 8.0}, threshold=THRESHOLD)
    with pytest.raises(CalibrationError, match="threshold"):
        agreement(critic, {"a": 8.0, "b": 5.0}, threshold=11.0)


def test_cli_round_trip_on_synthetic_scores(
    synthetic_outputs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "sheet"
    make = [
        "calibration-sheet",
        "--outputs", str(synthetic_outputs),
        "--critic-scores", str(SYNTHETIC_CRITIC_FILE),
        "--out-dir", str(out_dir),
    ]  # fmt: skip
    assert main(make) == 0
    csv_path = out_dir / "calibration_sheet.csv"
    assert (out_dir / "calibration_sheet.md").exists()

    agree = [
        "calibration-agreement",
        "--sheet", str(csv_path),
        "--critic-scores", str(SYNTHETIC_CRITIC_FILE),
        "--threshold", "8",
    ]  # fmt: skip
    # not filled yet: refused, no number
    assert main(agree) == 2
    assert "20 of 20 rows have no human_score" in capsys.readouterr().err

    # a second run must not wipe a sheet that may hold scores
    assert main(make) == 2
    assert "--overwrite" in capsys.readouterr().err

    # SYNTHETIC human scores: the critic's own numbers, so agreement is perfect by construction
    critic = json.loads(SYNTHETIC_CRITIC_FILE.read_text(encoding="utf-8"))["scores"]
    csv_path.write_text(_fill(csv_path.read_text(encoding="utf-8"), critic), encoding="utf-8")
    assert main(agree) == 0
    out = capsys.readouterr().out
    assert out.startswith("SYNTHETIC critic scores: this result measures nothing.")
    assert "20 items" in out
    assert "Cohen's kappa: 1.00" in out
