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


# ── propose: one topic into the inbox (C8, ADR-0019) ──────────────────────────


def test_propose_pauses_one_approve_draft_in_the_inbox_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json

    from langchain_core.callbacks import BaseCallbackHandler

    from measured_ops import load_policy
    from measured_ops.checkpoint import open_checkpointer
    from measured_ops.supervisor import build_supervisor
    from proofline.agent import ContentAgent

    class Marker(BaseCallbackHandler):
        runs = 0

        def on_chain_start(self, *args: object, **kwargs: object) -> None:
            Marker.runs += 1

    url = f"sqlite:///{tmp_path / 'ops.db'}"
    monkeypatch.setenv("MEASURED_OPS_INBOX_CHECKPOINTER_URL", url)
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    monkeypatch.setattr(cli, "masked_tracing_callbacks", lambda: [Marker()])
    llm = FakeLLM().queue(make_outline(), make_draft(), make_critique(9))
    deps = make_deps(tmp_path, llm)
    monkeypatch.setattr(cli, "build_deps", lambda settings: deps)

    result = CliRunner().invoke(
        cli.app, ["propose", "--topic", "Loops need exits", "--audience", "devs"]
    )

    assert result.exit_code == 0, result.output
    assert "waiting in the inbox" in result.output
    thread_id = result.output.strip().split()[-1]
    assert Marker.runs > 0

    async def state() -> object:
        async with open_checkpointer(url) as saver:
            graph = build_supervisor([ContentAgent(deps)], load_policy()).compile(
                checkpointer=saver
            )
            return await graph.aget_state({"configurable": {"thread_id": thread_id}})

    snap = asyncio.run(state())
    assert snap.next == ("gate",)  # type: ignore[attr-defined]
    [action] = snap.values["pending_actions"]  # type: ignore[attr-defined]
    assert action.type == "approve_draft"
    task = snap.values["task"]  # type: ignore[attr-defined]
    assert task.inputs == {"topic": "Loops need exits", "audience": "devs"}
    log_path = Path(str(tmp_path / "judge_log.jsonl"))
    [line] = [json.loads(x) for x in log_path.read_text().splitlines()]
    assert line["action_id"] == action.id


def test_propose_reports_a_failed_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEASURED_OPS_INBOX_CHECKPOINTER_URL", f"sqlite:///{tmp_path / 'o.db'}")
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path))

    result = CliRunner().invoke(cli.app, ["propose", "--topic", "   "])

    assert result.exit_code == 1
    assert "topic" in result.output


def test_propose_gives_up_after_the_revision_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    class HangingLLM(FakeLLM):
        async def generate(self, *args: object, **kwargs: object) -> object:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    monkeypatch.setenv("MEASURED_OPS_INBOX_CHECKPOINTER_URL", f"sqlite:///{tmp_path / 'o.db'}")
    monkeypatch.setenv("MEASURED_OPS_INBOX_REVISION_TIMEOUT_S", "0.2")
    monkeypatch.setattr(cli, "build_deps", lambda settings: make_deps(tmp_path, HangingLLM()))

    result = CliRunner().invoke(cli.app, ["propose", "--topic", "Loops need exits"])

    assert result.exit_code == 1
    assert "timed out after 0.2s" in result.output
