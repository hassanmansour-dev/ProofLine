# ADR-0006: Checkpointer factory (memory → SQLite → Postgres) with explicit serde allowlist

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W3

## Context
Local dev wants zero setup; later runs should live in Supabase Postgres.
Verified on langgraph 1.2.16: Pydantic models restored from a checkpoint emit
`Deserializing unregistered type ... will be blocked in a future version`
unless registered in `allowed_msgpack_modules`.

## Decision
- `open_checkpointer(url)` returns the right async saver by URL scheme:
  `memory://`, `sqlite:///path`, `postgresql://...`.
- All savers get `JsonPlusSerializer(allowed_msgpack_modules=STATE_MODELS)`,
  where `STATE_MODELS` lists `(module, class)` for every Pydantic model in state.
  `AsyncSqliteSaver.from_conn_string` doesn't take a serde, so the factory
  builds the saver from an `aiosqlite` connection directly.
- A test asserts every Pydantic type reachable from `ScriptState` is in the allowlist.

## Alternatives considered
- **Store `model_dump()` dicts in state** — no serde issue, but nodes lose types.
- **`allowed_msgpack_modules=True`** — allows everything; defeats the safety check.

## Consequences
- Good: forward-compatible with strict msgpack; dev → prod is a URL change.
- Bad: adding a state model needs a one-line registration (test catches misses).
