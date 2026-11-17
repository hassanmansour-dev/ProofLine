"""Regression baseline format: placeholders never gate (GROW-14)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from measured_evals.__main__ import main
from measured_evals.baseline import (
    BaselineError,
    compare,
    draft_from_report,
    load_baseline,
    parse_baseline,
)
from measured_evals.fake import careless_writer, grounded_writer
from measured_evals.golden import load_golden_set
from measured_evals.suites import run_set

ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER = ROOT / "evals" / "baselines" / "content_v0.placeholder.json"
GOLDEN = load_golden_set()
GOOD = run_set(GOLDEN, grounded_writer)
BAD = run_set(GOLDEN, careless_writer)


def _real(document: dict[str, Any]) -> dict[str, Any]:
    """A synthetic 'real' baseline for tests only: not a measurement of anything."""
    doc = copy.deepcopy(document)
    doc["status"], doc["approved_by"] = "real", "synthetic-test-reviewer"
    del doc["metrics"]["judge_mean"]
    for entry in (*doc["metrics"].values(), *doc["items"].values()):
        entry["status"], entry["source"] = "real", "synthetic test fixture"
    return doc


def test_committed_baseline_is_a_placeholder() -> None:
    baseline = load_baseline(PLACEHOLDER)
    assert baseline.status == "needs_eval"
    assert baseline.approved_by is None
    assert baseline.produced_by["placeholder"] is True
    assert not any(e.real for e in (*baseline.metrics.values(), *baseline.items.values()))
    assert baseline.metrics["judge_mean"].value is None
    assert len(baseline.placeholders()) == 22  # 2 metrics + 20 items


def test_placeholder_baseline_reports_but_never_enforces() -> None:
    comparison = compare(load_baseline(PLACEHOLDER), BAD)
    assert comparison.regressions
    assert comparison.enforced is False
    assert comparison.failed is False


def test_real_baseline_enforces_regressions() -> None:
    baseline = parse_baseline(_real(draft_from_report(GOOD, generator="synthetic")))
    assert compare(baseline, GOOD).failed is False
    worse = compare(baseline, BAD)
    assert worse.enforced
    assert worse.failed
    assert worse.regressions[0].what == "metrics.deterministic_pass_rate"
    assert len(worse.regressions) == 21


def test_judge_mean_uses_its_tolerance() -> None:
    doc = _real(draft_from_report(GOOD, generator="synthetic"))
    doc["metrics"]["judge_mean"] = {"value": 0.8, "status": "real", "source": "synthetic"}
    baseline = parse_baseline(doc)
    assert compare(baseline, GOOD, judge_mean=0.76).regressions == ()
    assert [r.what for r in compare(baseline, GOOD, judge_mean=0.7).regressions] == [
        "metrics.judge_mean"
    ]
    assert "metrics.judge_mean" in compare(baseline, GOOD).not_compared


def test_a_different_golden_set_is_refused() -> None:
    doc = draft_from_report(GOOD, generator="x") | {"golden_set": "other"}
    with pytest.raises(BaselineError, match="is for"):
        compare(parse_baseline(doc), GOOD)


def _doc() -> dict[str, Any]:
    return draft_from_report(GOOD, generator="x")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 9}, "schema_version"),
        ({"status": "approved"}, "status must be"),
        ({"status": "real"}, "approved_by"),
        ({"approved_by": "someone"}, "only for a real"),
        ({"surprise": 1}, "unknown keys"),
        ({"metrics": {"vibes": {"value": 1, "status": "real"}}}, "metrics must be"),
        ({"metrics": {"judge_mean": {"value": 1.5, "status": "needs_eval"}}}, r"in \[0, 1\]"),
        ({"metrics": {"judge_mean": {"value": True, "status": "needs_eval"}}}, r"in \[0, 1\]"),
        (
            {"metrics": {"judge_mean": {"value": 0.9, "status": "real"}}},
            "needs a value and a source",
        ),
        (
            {"metrics": {"judge_mean": {"value": None, "status": "unmeasured", "source": "x"}}},
            "no source",
        ),
        ({"items": {"a": {"value": 0.5, "status": "needs_eval"}}}, "true, false or null"),
        ({"tolerance": {"judge_mean": 2}}, "tolerance"),
    ],
)
def test_invalid_baselines_are_rejected(change: dict[str, Any], message: str) -> None:
    with pytest.raises(BaselineError, match=message):
        parse_baseline(_doc() | change)


def test_a_real_baseline_cannot_hold_placeholders() -> None:
    doc = _real(_doc())
    next(iter(doc["items"].values()))["status"] = "needs_eval"
    with pytest.raises(BaselineError, match="placeholder entries"):
        parse_baseline(doc)


def test_cli_passes_with_the_fake_and_fails_with_the_careless_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", "--baseline", str(PLACEHOLDER)]) == 0
    out = capsys.readouterr().out
    assert "20/20 items passed" in out
    assert "NOT ENFORCED" in out
    assert main(["run", "--writer", "fake-careless"]) == 1
    assert "0/20 items passed" in capsys.readouterr().out


def _draft_copy(tmp_path: Path) -> Path:
    """The bundled set with every approval removed: what an unreviewed set looks like."""
    from importlib import resources

    text = (
        resources.files("measured_evals")
        .joinpath("data", "golden", "content_v0.toml")
        .read_text(encoding="utf-8")
    )
    lines = [ln for ln in text.splitlines() if not ln.startswith(("approved_by", "approved_on"))]
    path = tmp_path / "draft.toml"
    path.write_text(
        "\n".join(lines).replace('status = "approved"', 'status = "needs_eval"') + "\n",
        encoding="utf-8",
    )
    return path


def test_cli_approved_only_on_a_draft_set_says_nothing_was_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--approved-only", "--golden", str(_draft_copy(tmp_path))]) == 0
    assert "nothing was measured" in capsys.readouterr().out


def test_cli_approved_only_runs_all_20_but_the_baseline_is_still_not_enforced(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Approved topics, fake writer, placeholder baseline: still no measured result.
    assert main(["run", "--approved-only", "--baseline", str(PLACEHOLDER)]) == 0
    out = capsys.readouterr().out
    assert "20/20 items passed" in out
    assert "NOT ENFORCED" in out


def test_cli_reads_saved_outputs_and_writes_a_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    for item in GOLDEN.items:
        (outputs / f"{item.id}.md").write_text(grounded_writer(item), encoding="utf-8")
    draft = tmp_path / "draft.json"
    assert main(["run", "--outputs", str(outputs), "--write-draft-baseline", str(draft)]) == 0
    capsys.readouterr()
    written = json.loads(draft.read_text(encoding="utf-8"))
    assert written["status"] == "needs_eval"
    assert {e["status"] for e in written["items"].values()} == {"needs_eval"}
    assert load_baseline(draft).real is False
