"""The supervisor's checkpointer, always with the OpsState serde allowlist (ADR-0006).

    async with open_checkpointer("postgresql://...") as saver:
        app = build_supervisor(agents, policy).compile(checkpointer=saver)

`memory://` needs nothing extra. `sqlite:///path` needs `langgraph-checkpoint-sqlite`
(dev group). `postgresql://` needs the `postgres` extra (ADR-0016); its tables are
created on first use. Proofline's own factory (`proofline.checkpointing`) serves
ScriptState; this one never imports proofline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from measured_ops.contracts import SERDE_ALLOWLIST


def serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST)


@asynccontextmanager
async def open_checkpointer(url: str) -> AsyncIterator[BaseCheckpointSaver[str]]:
    if url == "memory://":
        yield InMemorySaver(serde=serde())
        return
    if url.startswith("sqlite:///"):
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise RuntimeError("SQLite needs langgraph-checkpoint-sqlite installed") from exc
        async with aiosqlite.connect(url.removeprefix("sqlite:///")) as conn:
            yield AsyncSqliteSaver(conn, serde=serde())
        return
    if url.startswith(("postgresql://", "postgres://")):
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise RuntimeError("Postgres needs the extra: uv sync --extra postgres") from exc
        async with AsyncPostgresSaver.from_conn_string(url, serde=serde()) as saver:
            await saver.setup()  # idempotent: creates or migrates the tables
            yield saver
        return
    raise ValueError(
        "unsupported checkpointer URL: use memory://, sqlite:///<path> or postgresql://"
    )
