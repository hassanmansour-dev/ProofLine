"""`proofline run` end to end, with fakes injected in place of the real model."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from proofline import cli
from proofline.config import Settings
from tests.fakes import FakeLLM, make_deps, make_draft, make_outline


def test_run_prints_the_finished_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM().queue(make_outline(), make_draft())
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, llm))
    result = CliRunner().invoke(
        cli.app,
        ["run", "--topic", "Why every AI loop needs an exit", "--audience", "developers new to AI"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"# {make_draft().title}")
    assert "## Sources" in result.stdout
    assert "Why every AI loop needs an exit" in llm.calls[0].prompt.user


def test_run_reports_config_errors_without_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: Settings) -> None:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example)")

    monkeypatch.setattr(cli, "build_deps", broken)
    result = CliRunner().invoke(cli.app, ["run", "--topic", "anything"])
    assert result.exit_code == 1
    assert "error: ANTHROPIC_API_KEY is not set" in result.stderr
