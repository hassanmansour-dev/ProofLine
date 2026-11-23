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
