"""Judge log and posted marks in Postgres (C10, ADR-0023).

Needs MEASURED_OPS_TEST_DATABASE_URL: skipped locally without it, a failure in CI (like the
Postgres e2e cases). Each test works in its own schema.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest

from measured_ops.judge_log import JsonlJudgeLog, judge_log_from_env
from measured_ops.pg_logs import PostgresJudgeLog, PostgresPostedLog
from measured_ops.posted import JsonlPostedLog, posted_log_from_env

PG_ENV = "MEASURED_OPS_TEST_DATABASE_URL"


@pytest.fixture
def logs_url() -> Iterator[str]:
    base = os.environ.get(PG_ENV)
    if not base:
        if os.environ.get("CI"):
            pytest.fail(f"{PG_ENV} must be set in CI: the Postgres logs tests can't be skipped")
        pytest.skip(f"set {PG_ENV} to run the Postgres logs tests")
    import psycopg

    schema = f"t_{uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    sep = "&" if "?" in base else "?"
    try:
        yield f"{base}{sep}options={quote(f'-csearch_path={schema}')}"
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_judge_log_keeps_ids_scores_and_decisions_only(logs_url: str) -> None:
    log = PostgresJudgeLog(logs_url)
    log.record_critic("a1", {"accuracy": 8, "overall": 7.5})
    log.record_decision("a1", {"quality": 1.0, "pii": 0.0}, approved=False)
    log.record_decision("a2", None, approved=True)

    assert log.records() == [
        {"kind": "critic", "action_id": "a1", "critic": {"accuracy": 8, "overall": 7.5}},
        {
            "kind": "decision",
            "action_id": "a1",
            "eval": {"quality": 1.0, "pii": 0.0},
            "approved": False,
        },
        {"kind": "decision", "action_id": "a2", "eval": None, "approved": True},
    ]
    with pytest.raises(ValueError, match="must be a number"):
        log.record_critic("a3", {"accuracy": "high"})  # type: ignore[dict-item]


def test_importing_the_jsonl_log_twice_adds_it_once(logs_url: str, tmp_path: Path) -> None:
    path = tmp_path / "judge_log.jsonl"
    jsonl = JsonlJudgeLog(path)
    jsonl.record_critic("a1", {"overall": 7.0})
    jsonl.record_decision("a1", {"quality": 1.0}, approved=True)
    log = PostgresJudgeLog(logs_url)

    assert log.import_jsonl(path) == 2
    assert log.import_jsonl(path) == 0
    assert [r["kind"] for r in log.records()] == ["critic", "decision"]


def test_posted_marks(logs_url: str, tmp_path: Path) -> None:
    posted = PostgresPostedLog(logs_url)
    posted.mark("a1")
    posted.mark("a1")  # twice is harmless
    assert posted.posted() == {"a1"}

    path = tmp_path / "posted.jsonl"
    JsonlPostedLog(path).mark("a2")
    path.open("a").write("not json\n")
    assert posted.import_jsonl(path) == 1
    assert posted.import_jsonl(path) == 0
    assert posted.posted() == {"a1", "a2"}


def test_the_url_selects_postgres_for_both(logs_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEASURED_OPS_LOGS_URL", logs_url)
    assert isinstance(judge_log_from_env(), PostgresJudgeLog)
    assert isinstance(posted_log_from_env(), PostgresPostedLog)


def test_without_the_url_the_jsonl_files_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEASURED_OPS_LOGS_URL", raising=False)
    assert isinstance(judge_log_from_env(), JsonlJudgeLog)
    assert isinstance(posted_log_from_env(), JsonlPostedLog)


def test_only_postgres_urls() -> None:
    with pytest.raises(ValueError, match="postgresql://"):
        PostgresPostedLog("sqlite:///x.db")
    assert json  # (keeps the import used when the Postgres cases skip)


# ---- review: no connection at construction, and never on the event loop -------------------


class _FakeConn:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> _FakeConn:
        self.log.append(sql.split()[0])
        return self

    description = None


def test_constructing_a_postgres_log_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    from measured_ops import pg_logs

    url = "postgresql://test@127.0.0.1:1/none"
    monkeypatch.setenv(PG_ENV, url)  # the root guard allows only the test database
    connections: list[str] = []
    statements: list[str] = []

    def fake_connect(u: str) -> _FakeConn:
        connections.append(u)
        return _FakeConn(statements)

    monkeypatch.setattr(pg_logs, "_connect", fake_connect)
    log = pg_logs.PostgresJudgeLog(url)
    assert connections == []  # nothing until the first write

    log.record_decision("a1", None, approved=True)
    log.record_decision("a2", None, approved=True)
    assert statements == ["CREATE", "INSERT", "INSERT"]  # the schema once, lazily


@pytest.mark.anyio
async def test_the_inbox_writes_its_logs_off_the_event_loop() -> None:
    import threading
    from typing import Any

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from ops_fakes import ScriptedAgent, make_task

    from measured_ops import initial_ops_state, load_policy
    from measured_ops.contracts import SERDE_ALLOWLIST
    from measured_ops.inbox.service import Inbox
    from measured_ops.supervisor import build_supervisor

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    class SlowLogs:  # stands in for the synchronous Postgres adapters
        def record_critic(self, action_id: str, scores: Any) -> None:
            seen["critic"] = threading.get_ident()

        def record_decision(self, action_id: str, eval_scores: Any, approved: bool) -> None:
            seen["decision"] = threading.get_ident()

        def mark(self, action_id: str) -> None:
            seen["mark"] = threading.get_ident()

        def posted(self) -> set[str]:
            seen["posted"] = threading.get_ident()
            return set()

    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    agent = ScriptedAgent([("approve_draft", "medium", "## LinkedIn post\nText.")])
    graph = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
    cfg: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    await graph.ainvoke(initial_ops_state(make_task()), cfg)
    logs = SlowLogs()
    inbox = Inbox(graph, load_policy(), callbacks=[], judge_log=logs, posted=logs)
    [item] = await inbox.list_pending()

    await inbox.decide("t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True)
    [ready] = await inbox.list_ready()
    await inbox.mark_posted(ready.action_id)

    assert set(seen) == {"decision", "posted", "mark"}
    assert all(thread != loop_thread for thread in seen.values()), seen
