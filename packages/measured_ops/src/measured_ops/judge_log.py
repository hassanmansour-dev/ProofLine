"""Judge-agreement log (C8, ADR-0019): how the critic scored a draft, what the eval node
said, and what Hassan decided, joined later on `action_id` (GROW-13).

Only ids, scores and a bool are written: **never draft text, previews or targets**. The
port has no parameter that could carry them. One JSON object per line, appended (short
lines with O_APPEND, so the inbox and a `proofline propose` process can share a file).

    {"kind": "critic",   "action_id": ..., "critic": {criterion: score, ..., "overall": x}}
    {"kind": "decision", "action_id": ..., "eval": {metric: score} | null, "approved": bool}

Default path `.data/judge_log.jsonl` (gitignored); `MEASURED_OPS_JUDGE_LOG_PATH` sets
another (the private repo's).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

DEFAULT_PATH = Path(".data/judge_log.jsonl")
ENV = "MEASURED_OPS_JUDGE_LOG_PATH"


class JudgeLog(Protocol):
    def record_critic(self, action_id: str, scores: Mapping[str, float]) -> None: ...

    def record_decision(
        self, action_id: str, eval_scores: Mapping[str, float] | None, approved: bool
    ) -> None: ...


def _scores(scores: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in scores.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"score names must be non-empty strings, got {name!r}")
        if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(value):
            raise ValueError(f"score for {name!r} must be a number, got {value!r}")
        out[name] = value
    return out


class JsonlJudgeLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _append(self, record: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def record_critic(self, action_id: str, scores: Mapping[str, float]) -> None:
        self._append({"kind": "critic", "action_id": action_id, "critic": _scores(scores)})

    def record_decision(
        self, action_id: str, eval_scores: Mapping[str, float] | None, approved: bool
    ) -> None:
        self._append(
            {
                "kind": "decision",
                "action_id": action_id,
                "eval": None if eval_scores is None else _scores(eval_scores),
                "approved": bool(approved),
            }
        )


class NullJudgeLog:
    """Records nothing (tests, or a deployment that doesn't keep the log)."""

    def record_critic(self, action_id: str, scores: Mapping[str, float]) -> None:
        del action_id, scores

    def record_decision(
        self, action_id: str, eval_scores: Mapping[str, float] | None, approved: bool
    ) -> None:
        del action_id, eval_scores, approved


async def setup_log(log: object) -> None:
    """Run a log adapter's `setup()` if it has one (the Postgres adapters do), in a worker
    thread, and let it raise: a log that can't be reached must fail at start-up. Write
    errors after that are logged and never undo a decision (ADR-0023)."""
    setup = getattr(log, "setup", None)
    if callable(setup):
        await asyncio.to_thread(setup)


def judge_log_from_env() -> JudgeLog:
    """Postgres when `MEASURED_OPS_LOGS_URL` is set (C10), else the JSONL file."""
    if os.environ.get("MEASURED_OPS_LOGS_URL", "").strip():
        from measured_ops.pg_logs import PostgresJudgeLog

        return PostgresJudgeLog(os.environ["MEASURED_OPS_LOGS_URL"].strip())
    raw = os.environ.get(ENV)
    return JsonlJudgeLog(Path(raw) if raw else DEFAULT_PATH)
