"""Judge log and posted marks in Postgres (C10, ADR-0023; PLAN C10, ADR-0019/0020).

Same ports and the same rules as the JSONL adapters (`judge_log.py`, `posted.py`): ids,
scores and a bool in the judge log, an action id and a time in the posted marks; never
text, previews or targets. Selected by `MEASURED_OPS_LOGS_URL=postgresql://...`; the JSONL
files stay the default. Needs the `postgres` extra (psycopg).

Tables (created if missing, idempotently):

    measured_ops_judge_log(id, recorded_at, kind, action_id, scores jsonb, approved, line_hash)
    measured_ops_posted(action_id primary key, posted_at)

`import_jsonl` copies the existing local files in. Re-running it adds nothing: each judge
log line is keyed by the hash of its own text, and a posted mark by its action id.

The ports are synchronous (the JSONL adapters write files inline), so each write here is
one short synchronous round trip on its own connection (10 s connect timeout). Callers on
an event loop (the inbox, the content agent) run every log call in a worker thread
(`asyncio.to_thread`), so a slow database never blocks the loop. Nothing connects when an
adapter is constructed: the tables are created on first use.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from measured_ops.judge_log import JudgeLog, _scores

ENV = "MEASURED_OPS_LOGS_URL"

SCHEMA = """
CREATE TABLE IF NOT EXISTS measured_ops_judge_log (
    id bigserial PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('critic', 'decision')),
    action_id text NOT NULL CHECK (action_id <> ''),
    scores jsonb,
    approved boolean,
    line_hash text NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS measured_ops_judge_log_action ON measured_ops_judge_log (action_id);
CREATE TABLE IF NOT EXISTS measured_ops_posted (
    action_id text PRIMARY KEY CHECK (action_id <> ''),
    posted_at timestamptz NOT NULL DEFAULT now()
);
"""


def _connect(url: str) -> Any:
    import psycopg  # the `postgres` extra

    return psycopg.connect(url, connect_timeout=10, autocommit=True)


class _Pg:
    def __init__(self, url: str) -> None:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ValueError("MEASURED_OPS_LOGS_URL must be a postgresql:// URL")
        self.url = url
        self._ready = False
        self._setup_lock = threading.Lock()

    def _setup(self) -> None:
        """Create the tables once, on first use (never in __init__: no I/O at construction)."""
        if self._ready:
            return
        with self._setup_lock:
            if not self._ready:
                with _connect(self.url) as conn:
                    conn.execute(SCHEMA)
                self._ready = True

    def _execute(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        self._setup()
        with _connect(self.url) as conn:
            cur = conn.execute(sql, params)
            return list(cur.fetchall()) if cur.description else []


class PostgresJudgeLog(_Pg):
    """`JudgeLog` in Postgres."""

    def _insert(
        self,
        kind: str,
        action_id: str,
        scores: dict[str, float] | None,
        approved: bool | None,
        line_hash: str,
    ) -> int:
        if not action_id:
            raise ValueError("action_id is required")
        rows = self._execute(
            "INSERT INTO measured_ops_judge_log (kind, action_id, scores, approved, line_hash) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (line_hash) DO NOTHING RETURNING id",
            (kind, action_id, None if scores is None else json.dumps(scores), approved, line_hash),
        )
        return len(rows)

    def record_critic(self, action_id: str, scores: Mapping[str, float]) -> None:
        self._insert("critic", action_id, _scores(scores), None, uuid4().hex)

    def record_decision(
        self, action_id: str, eval_scores: Mapping[str, float] | None, approved: bool
    ) -> None:
        scores = None if eval_scores is None else _scores(eval_scores)
        self._insert("decision", action_id, scores, bool(approved), uuid4().hex)

    def records(self) -> list[dict[str, Any]]:
        """Every line, oldest first, in the JSONL shape (for GROW-13 and the tests)."""
        out: list[dict[str, Any]] = []
        for kind, action_id, scores, approved in self._execute(
            "SELECT kind, action_id, scores, approved FROM measured_ops_judge_log ORDER BY id",
            (),
        ):
            if kind == "critic":
                out.append({"kind": kind, "action_id": action_id, "critic": scores})
            else:
                out.append(
                    {"kind": kind, "action_id": action_id, "eval": scores, "approved": approved}
                )
        return out

    def import_jsonl(self, path: Path) -> int:
        """Copy a JSONL judge log in; returns the number of new lines. Idempotent."""
        added = 0
        if not path.exists():
            return 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            digest = "jsonl:" + hashlib.sha256(line.strip().encode()).hexdigest()
            if record.get("kind") == "critic":
                added += self._insert(
                    "critic", record["action_id"], _scores(record["critic"]), None, digest
                )
            elif record.get("kind") == "decision":
                scores = record.get("eval")
                added += self._insert(
                    "decision",
                    record["action_id"],
                    None if scores is None else _scores(scores),
                    bool(record["approved"]),
                    digest,
                )
            else:
                raise ValueError(f"{path}: a line that isn't a critic or decision record")
        return added


class PostgresPostedLog(_Pg):
    """Posted marks in Postgres: the same `mark` / `posted` as `JsonlPostedLog`."""

    def mark(self, action_id: str, posted_at: datetime | None = None) -> None:
        if not action_id:
            raise ValueError("action_id is required")
        self._execute(
            "INSERT INTO measured_ops_posted (action_id, posted_at) "
            "VALUES (%s, COALESCE(%s, now())) ON CONFLICT (action_id) DO NOTHING",
            (action_id, posted_at),
        )

    def posted(self) -> set[str]:
        return {row[0] for row in self._execute("SELECT action_id FROM measured_ops_posted", ())}

    def import_jsonl(self, path: Path) -> int:
        """Copy a JSONL posted file in; returns the number of new marks. Idempotent."""
        if not path.exists():
            return 0
        before = len(self.posted())
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # the JSONL reader skips unreadable lines too
            action_id = record.get("action_id")
            if isinstance(action_id, str) and action_id:
                when = record.get("posted_at")
                self.mark(action_id, datetime.fromisoformat(when) if when else None)
        return len(self.posted()) - before


def logs_url() -> str | None:
    raw = os.environ.get(ENV, "").strip()
    return raw or None


def pg_judge_log_from_env() -> JudgeLog | None:
    url = logs_url()
    return PostgresJudgeLog(url) if url else None
