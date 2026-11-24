"""Posted marks (C8, ADR-0020): Hassan says he posted an approved draft; nothing else.

Recording a mark calls nothing outside the system and never touches a run: it appends
`{"action_id", "posted_at"}` to a local JSONL file (`MEASURED_OPS_POSTED_PATH`, default
`.data/posted.jsonl`, gitignored). A run's state isn't updated, because writing to a
finished thread re-runs the graph's routing. Marking twice is harmless.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

DEFAULT_PATH = Path(".data/posted.jsonl")
ENV = "MEASURED_OPS_POSTED_PATH"


class PostedLog(Protocol):
    def mark(self, action_id: str) -> None: ...

    def posted(self) -> set[str]: ...


class JsonlPostedLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def mark(self, action_id: str) -> None:
        if not action_id:
            raise ValueError("action_id is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"action_id": action_id, "posted_at": datetime.now(UTC).isoformat()})
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def posted(self) -> set[str]:
        """Action ids marked as posted. A line that can't be read is skipped."""
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line).get("action_id")
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(value, str) and value:
                ids.add(value)
        return ids


def posted_log_from_env() -> PostedLog:
    """Postgres when `MEASURED_OPS_LOGS_URL` is set (C10), else the JSONL file."""
    if os.environ.get("MEASURED_OPS_LOGS_URL", "").strip():
        from measured_ops.pg_logs import PostgresPostedLog

        return PostgresPostedLog(os.environ["MEASURED_OPS_LOGS_URL"].strip())
    raw = os.environ.get(ENV)
    return JsonlPostedLog(Path(raw) if raw else DEFAULT_PATH)
