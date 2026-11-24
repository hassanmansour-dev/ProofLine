"""Daily digest and the worker (C10, ADR-0023): read-only digests, a schedule that follows
daylight saving, and the kill switch commands."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from ops_fakes import ScriptedAgent, make_task

from measured_evals import ModelPrice, Usage
from measured_ops import ApprovalDecision, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.digest import FileDigestSink, build_digest
from measured_ops.inbox.service import Inbox
from measured_ops.killswitch import KillSwitch
from measured_ops.supervisor import build_supervisor
from measured_ops.worker import main, next_run, parse_times

pytestmark = pytest.mark.anyio
TZ = ZoneInfo("America/Toronto")
PRICES = {
    "model-a": ModelPrice(
        input=1.0, output=2.0, cache_read=0.1, cache_write_5m=1.25, cache_write_1h=2.0
    )
}


def _cfg(thread: str) -> Any:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 40}


async def _graph_with_runs() -> Any:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    agent = ScriptedAgent([("publish_public", "medium", "Post.", "someone@example.com")])
    graph = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
    state = initial_ops_state(make_task(goal="Explain exits in agent loops."))
    state["usage"] = [Usage(agent="echo", model="model-a", input_tokens=1000, output_tokens=500)]
    await graph.ainvoke(state, _cfg("waiting"))
    draft = ScriptedAgent([("approve_draft", "medium", "## LinkedIn post\nText.")])
    dgraph = build_supervisor([draft], load_policy()).compile(checkpointer=saver)
    out = await dgraph.ainvoke(initial_ops_state(make_task(goal="A draft to paste.")), _cfg("d"))
    [intr] = out["__interrupt__"]
    decision = ApprovalDecision(action_id=intr.value["action_id"], approved=True)
    await dgraph.ainvoke(Command(resume=decision.model_dump()), _cfg("d"))
    return graph


async def test_digest_lists_what_waits_and_what_it_cost(tmp_path: Path) -> None:
    graph = await _graph_with_runs()
    inbox = Inbox(graph, load_policy(), prices=PRICES, callbacks=[])
    switch = KillSwitch(tmp_path / "kill")

    digest = await build_digest(inbox, prices=PRICES, kill_switch=switch)

    md = digest.markdown
    assert digest.waiting == 1
    assert "## Waiting for you (1)\n- Explain exits in agent loops. · publish_public" in md
    assert "## Approved drafts not posted yet (1)\n- A draft to paste." in md
    assert "- echo: 1,500 tokens · $0.0020" in md  # 1000 tokens at $1/M + 500 at $2/M
    assert "Kill switch" not in md
    assert "someone@example.com" not in md  # targets never appear


async def test_digest_says_when_the_kill_switch_is_on(tmp_path: Path) -> None:
    graph = await _graph_with_runs()
    switch = KillSwitch(tmp_path / "kill")
    switch.engage("paused for the weekend")
    digest = await build_digest(
        Inbox(graph, load_policy(), prices=PRICES, callbacks=[]), prices=PRICES, kill_switch=switch
    )
    assert "**Kill switch is ON**: no agent runs." in digest.markdown
    assert "paused for the weekend" in digest.markdown


async def test_unpriced_models_show_unknown_cost(tmp_path: Path) -> None:
    graph = await _graph_with_runs()
    digest = await build_digest(
        Inbox(graph, load_policy(), prices={}, callbacks=[]),
        prices={},
        kill_switch=KillSwitch(tmp_path / "k"),
    )
    assert "- echo: 1,500 tokens · unknown" in digest.markdown


async def test_runs_outside_the_window_are_not_costed(tmp_path: Path) -> None:
    graph = await _graph_with_runs()
    later = datetime.now(UTC) + timedelta(days=2)
    digest = await build_digest(
        Inbox(graph, load_policy(), prices=PRICES, callbacks=[]),
        prices=PRICES,
        kill_switch=KillSwitch(tmp_path / "k"),
        now=later,
    )
    assert "- No model calls." in digest.markdown


async def test_file_sink(tmp_path: Path) -> None:
    graph = await _graph_with_runs()
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    digest = await build_digest(
        Inbox(graph, load_policy(), prices=PRICES, callbacks=[]),
        prices=PRICES,
        kill_switch=KillSwitch(tmp_path / "k"),
        now=now,
    )
    where = FileDigestSink(tmp_path / "digests").deliver(digest)
    assert where.endswith("2026-11-24-1200.md")
    text = (tmp_path / "digests" / "2026-11-24-1200.md").read_text()
    assert text.startswith("# Measured Ops digest — 2026-11-24 12:00 UTC")


def test_schedule_is_7am_and_9pm_local_and_follows_daylight_saving() -> None:
    times = parse_times(["21:00", "07:00"])
    assert times == [time(7), time(21)]
    morning = datetime(2026, 11, 24, 6, 59, tzinfo=TZ)
    assert next_run(morning, times, TZ) == datetime(2026, 11, 24, 7, 0, tzinfo=TZ)
    assert next_run(datetime(2026, 11, 24, 7, 0, tzinfo=TZ), times, TZ).hour == 21
    late = datetime(2026, 11, 24, 22, 0, tzinfo=TZ)
    assert next_run(late, times, TZ) == datetime(2026, 11, 25, 7, 0, tzinfo=TZ)
    # the night clocks go back (2026-11-01): 7 AM local is 12:00 UTC, not 11:00
    before = datetime(2026, 10, 31, 22, 0, tzinfo=TZ)
    assert next_run(before, times, TZ).astimezone(UTC) == datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="HH:MM"):
        parse_times(["7am"])


def test_kill_release_and_status_commands(capsys: pytest.CaptureFixture[str]) -> None:
    switch = KillSwitch.from_env()  # the root conftest's tmp path
    assert main(["kill", "--reason", "testing"]) == 0
    assert switch.engaged()
    assert main(["status"]) == 0
    assert main(["release"]) == 0
    assert not switch.engaged()
    assert main(["release"]) == 0
    out = capsys.readouterr().out
    assert "kill switch: ON" in out
    assert "kill switch released" in out
    assert "it was off" in out


def test_digest_command_reads_the_inbox_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MEASURED_OPS_INBOX_CHECKPOINTER_URL", f"sqlite:///{tmp_path / 'cp.db'}")
    assert main(["digest", "--out", str(tmp_path / "digests")]) == 0
    [written] = list((tmp_path / "digests").glob("*.md"))
    assert "## Waiting for you (0)" in written.read_text()
    assert str(written) in capsys.readouterr().out


def test_import_logs_needs_a_postgres_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MEASURED_OPS_LOGS_URL", raising=False)
    assert main(["import-logs"]) == 2
    assert "MEASURED_OPS_LOGS_URL" in capsys.readouterr().err
