"""Checkpointer factory with an explicit serde allowlist (ADR-0006).

async with open_checkpointer("sqlite:///.data/checkpoints.db") as saver:
    app = build_graph().compile(checkpointer=saver)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from proofline.schemas import STATE_MODELS

SERDE_ALLOWLIST: list[tuple[str, str]] = [(m.__module__, m.__qualname__) for m in STATE_MODELS]


def make_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST)


@asynccontextmanager
async def open_checkpointer(url: str) -> AsyncIterator[BaseCheckpointSaver[str]]:
    serde = make_serde()

    if url.startswith("memory://"):
        yield InMemorySaver(serde=serde)
        return

    if url.startswith("sqlite:///"):
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = Path(url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        try:
            yield AsyncSqliteSaver(conn, serde=serde)
        finally:
            await conn.close()
        return

    if url.startswith(("postgresql://", "postgres://")):
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install the postgres extra: uv sync --extra postgres") from exc
        async with AsyncPostgresSaver.from_conn_string(url, serde=serde) as saver:
            await saver.setup()
            yield saver
        return

    raise ValueError(
        f"Unsupported checkpointer URL: {url!r} (memory://, sqlite:///, postgresql://)"
    )
