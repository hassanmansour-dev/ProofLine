"""`proofline run` end to end, with fakes injected in place of the real model."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from proofline import cli
from proofline.config import Settings
from tests.fakes import FakeLLM, make_critique, make_deps, make_draft, make_outline, make_settings


@pytest.fixture(autouse=True)
def _no_real_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's .env out of the test session: load_dotenv writes to os.environ."""
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(tmp_path))


def test_run_prints_the_finished_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM().queue(make_outline(), make_draft(), make_critique(9))
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
    llm = FakeLLM().queue(make_outline(), make_draft(), make_critique(9))
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, llm))
    result = CliRunner().invoke(cli.app, ["run", "--topic", "anything"])
    assert "research is stubbed" in result.stderr


def test_inbox_serves_the_content_agent_and_the_demo_echo_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from measured_ops.inbox import server

    served: list[list[str]] = []
    logs: list[object] = []

    def fake_serve(agents: list, **kw: object) -> int:
        served.append([a.name for a in agents])
        logs.extend([agents[0].judge_log, kw["judge_log"]])
        return 0

    monkeypatch.setattr(server, "serve", fake_serve)
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path))

    result = CliRunner().invoke(cli.app, ["inbox"])

    assert result.exit_code == 0, result.output
    assert served == [["content", "echo"]]
    assert logs[0] is logs[1]  # one judge log: the agent's critic lines, the inbox's decisions


# ── Masked tracing (C8, ADR-0018/0019) ─────────────────────────────────────────


def test_run_passes_the_masked_tracer_to_the_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.callbacks import BaseCallbackHandler

    class Marker(BaseCallbackHandler):
        def __init__(self) -> None:
            self.runs = 0

        def on_chain_start(self, *args: object, **kwargs: object) -> None:
            self.runs += 1

    marker = Marker()
    monkeypatch.setattr(cli, "masked_tracing_callbacks", lambda: [marker])
    llm = FakeLLM().queue(make_outline(), make_draft(), make_critique(9))
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, llm))

    result = CliRunner().invoke(cli.app, ["run", "--topic", "Loops need exits"])

    assert result.exit_code == 0, result.output
    assert marker.runs > 0  # the pipeline's runs reached the tracer the CLI passed


def test_cli_tracing_is_masked_and_replaces_the_default_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.callbacks import CallbackManager
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import utils as ls_utils

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-not-real")
    ls_utils.get_env_var.cache_clear()
    try:
        callbacks = cli.masked_tracing_callbacks()
        manager = CallbackManager.configure(inheritable_callbacks=callbacks)
        tracers = [h for h in manager.handlers if isinstance(h, LangChainTracer)]
        assert len(callbacks) == 1
        assert tracers == callbacks
        masked = tracers[0].client._hide_run_inputs({"draft": "Mail jane.doe@example.com"})
        assert "jane.doe@example.com" not in repr(masked)
    finally:
        ls_utils.get_env_var.cache_clear()
