"""`proofline golden`: the real pipeline on golden items, saved for `measured_evals run`.

Fake models only (fixtures, never evidence): these prove the wiring and the file shape,
not the pipeline's quality.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from measured_evals.calibration import load_critic_scores
from measured_evals.golden import GoldenSet, load_golden_set
from measured_evals.suites import run_item as check_item
from proofline import cli
from proofline.golden import CRITIC_FILE, run_golden, script_text
from proofline.research.fixture import FixtureResearcher
from proofline.schemas import ExtractedFindings, Finding
from tests.fakes import FakeLLM, make_critique, make_deps, make_draft, make_outline, make_settings

pytestmark = pytest.mark.anyio
GOLDEN = load_golden_set()
STANDARD = GOLDEN.by_id("loop-needs-an-exit")
NO_SOURCES = GOLDEN.by_id("unanswerable-no-sources")


def _findings() -> ExtractedFindings:
    return ExtractedFindings(
        findings=[Finding(claim="A loop needs an exit.", source_ids=["S1"], confidence="high")],
        gaps=[],
    )


def _set(*items: Any) -> GoldenSet:
    return GoldenSet(name=GOLDEN.name, description="", items=tuple(items))


async def test_fixture_researcher_keeps_the_items_ids_and_sources_as_data(tmp_path: Path) -> None:
    llm = FakeLLM().queue(_findings())
    deps = make_deps(tmp_path, llm)
    notes = await FixtureResearcher(STANDARD.id, STANDARD.sources, llm).research(
        STANDARD.topic, make_outline(), deps.settings.run_config()
    )

    assert [s.id for s in notes.sources] == ["S1", "S2", "S3"]
    assert notes.sources[0].content == STANDARD.sources[0].text
    assert notes.sources[0].url == "golden:loop-needs-an-exit#S1"
    [call] = llm.calls
    assert call.role == "researcher"
    assert "<source" in call.prompt.user  # untrusted data, wrapped like web sources


async def test_no_sources_means_no_call_and_the_questions_are_gaps(tmp_path: Path) -> None:
    llm = FakeLLM()
    outline = make_outline()
    deps = make_deps(tmp_path, llm)
    notes = await FixtureResearcher(NO_SOURCES.id, NO_SOURCES.sources, llm).research(
        NO_SOURCES.topic, outline, deps.settings.run_config()
    )
    assert notes.sources == []
    assert notes.gaps == list(outline.key_questions)
    assert llm.calls == []


def test_the_saved_shape_is_what_the_checks_split_on() -> None:
    text = script_text(make_draft(cited=["S1", "S2"]))
    result = check_item(STANDARD, text)
    by_kind = {r.kind: r.passed for r in result.results if r.group == "suite"}
    assert by_kind["structure"] is True
    assert by_kind["citations_resolve"] is True
    assert "Sources" not in text


async def test_runs_saves_and_records_critic_scores(tmp_path: Path) -> None:
    llm = FakeLLM().queue(
        make_outline(), _findings(), make_draft(cited=["S1", "S2"]), make_critique(9)
    )
    deps = make_deps(tmp_path, llm)
    out = tmp_path / "outputs" / "golden"

    run = await run_golden(_set(STANDARD), deps, out)

    assert run.written == [STANDARD.id]
    saved = (out / f"{STANDARD.id}.md").read_text()
    assert saved.startswith(f"# {make_draft().title}\n\n")
    assert [c.role for c in llm.calls] == ["planner", "researcher", "writer", "critic"]
    critic = load_critic_scores(out / CRITIC_FILE)  # the calibration sheet's input
    assert critic.scores == {STANDARD.id: make_critique(9).overall}
    assert not critic.synthetic
    assert "writer" in (critic.source or "")


async def test_resumes_without_paying_twice_and_overwrites_when_asked(tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "golden"
    first = FakeLLM().queue(make_outline(), _findings(), make_draft(), make_critique(9))
    await run_golden(_set(STANDARD), make_deps(tmp_path, first), out)

    again = FakeLLM()  # nothing queued: any call would fail
    run = await run_golden(_set(STANDARD), make_deps(tmp_path, again), out)
    assert (run.skipped, again.calls) == ([STANDARD.id], [])

    redo = FakeLLM().queue(make_outline(), _findings(), make_draft(), make_critique(10))
    run = await run_golden(_set(STANDARD), make_deps(tmp_path, redo), out, overwrite=True)
    assert run.written == [STANDARD.id]
    assert json.loads((out / CRITIC_FILE).read_text())["scores"] == {
        STANDARD.id: make_critique(10).overall
    }


async def test_one_failed_item_doesnt_stop_the_others(tmp_path: Path) -> None:
    # the first item gets no findings queued, so its research call fails
    llm = FakeLLM().queue(make_outline(), make_outline(), make_draft(), make_critique(9))
    out = tmp_path / "outputs" / "golden"
    run = await run_golden(_set(STANDARD, NO_SOURCES), make_deps(tmp_path, llm), out)

    assert list(run.failed) == [STANDARD.id]
    assert run.written == [NO_SOURCES.id]
    assert not (out / f"{STANDARD.id}.md").exists()


async def test_approved_only_by_default_and_unknown_ids_refused(tmp_path: Path) -> None:
    draft_item = replace(STANDARD, id="not-approved", status="needs_eval", approved_by=None)
    llm = FakeLLM()
    out = tmp_path / "outputs" / "golden"
    run = await run_golden(_set(draft_item), make_deps(tmp_path, llm), out)
    assert (run.written, run.skipped, run.failed) == ([], [], {})
    with pytest.raises(ValueError, match="no golden item"):
        await run_golden(_set(STANDARD), make_deps(tmp_path, llm), out, only={"nope"})


def test_cli_refuses_an_out_dir_outside_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, FakeLLM()))
    result = CliRunner().invoke(cli.app, ["golden", "--out", str(tmp_path / "elsewhere")])
    assert result.exit_code == 1
    assert "must be under" in result.output


def test_cli_runs_one_item_and_prints_the_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM().queue(make_outline(), _findings(), make_draft(), make_critique(9))
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, llm))
    out = tmp_path / "outputs" / "golden"

    result = CliRunner().invoke(cli.app, ["golden", "--out", str(out), "--item", STANDARD.id])

    assert result.exit_code == 0, result.output
    assert "saved 1, skipped 0, failed 0" in result.output
    assert f"--outputs {out} --write-draft-baseline" in result.output
    assert (out / f"{STANDARD.id}.md").exists()
