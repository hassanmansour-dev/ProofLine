"""`proofline run` end to end, with fakes injected in place of the real model."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from proofline import cli
from proofline.config import Settings
from tests.fakes import FakeLLM, make_deps, make_draft, make_outline, make_settings


@pytest.fixture(autouse=True)
def _no_real_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's .env out of the test session: load_dotenv writes to os.environ."""
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(tmp_path))


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


def test_run_reports_out_of_range_settings_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(tmp_path, max_revisions=20))
    result = CliRunner().invoke(cli.app, ["run", "--topic", "anything"])
    assert result.exit_code == 1
    assert result.stderr.startswith("error:")
    assert "max_revisions" in result.stderr
    assert "Traceback" not in result.stderr


def test_run_says_when_research_is_stubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM().queue(make_outline(), make_draft())
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, llm))
    result = CliRunner().invoke(cli.app, ["run", "--topic", "anything"])
    assert "research is stubbed" in result.stderr
