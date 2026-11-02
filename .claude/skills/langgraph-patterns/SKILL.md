---
name: langgraph-patterns
description: Verified LangGraph 1.2 API patterns for this repo — StateGraph, reducers, async nodes with runtime context, conditional edges with path maps, RetryPolicy, interrupt/Command resume, streaming, checkpointers (memory/SQLite/Postgres) with the serde allowlist, state history, mermaid. Load before writing or reviewing any LangGraph code.
user-invocable: false
---

# LangGraph patterns (verified on langgraph 1.2.16, checkpoint-sqlite 3.1.2, checkpoint-postgres 3.2.0)

Every snippet below was executed against those versions on 2026-10-20. If the
installed version differs (`uv run python -c "import importlib.metadata as m; print(m.version('langgraph'))"`),
re-verify with the `api-verifier` agent before relying on a detail.

## Imports
```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command, RetryPolicy
from langgraph.runtime import Runtime
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
```

## State with a reducer
```python
import operator
from typing import Annotated, TypedDict


class ScriptState(TypedDict, total=False):
    revision_count: int  # last write wins
    history: Annotated[list[RevisionRecord], operator.add]  # appends across nodes
```
Return `{"history": [record]}` (a list) — the reducer concatenates.

## Async node with runtime context (dependency injection)
```python
@dataclass
class Deps:
    llm: StructuredLLM
    search: SearchClient
    settings: Settings


async def planner(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    outline = await runtime.context.llm.generate(prompt, Outline, role="planner")
    return {"outline": outline}  # partial update; never mutate `state`


builder = StateGraph(ScriptState, context_schema=Deps)
builder.add_node("planner", planner)
builder.add_node("researcher", researcher, retry_policy=RetryPolicy(max_attempts=3))
```
Invoke with `await app.ainvoke(inputs, config, context=deps)`. Context is **not**
checkpointed — pass it again on every resume call.

Inside a node: `runtime.execution_info.thread_id`, `.checkpoint_id`, `.node_attempt`
(attempt number under RetryPolicy). A node may also take
`(state, config: RunnableConfig, runtime)` — both signatures verified.

## Conditional edges with a path map
```python
builder.add_conditional_edges(
    "critic", route_after_critic, {"writer": "writer", "human_review": "human_review"}
)
builder.add_conditional_edges("human_review", route_after_human, ["writer", "finalizer"])
```
Router signature: `def route_after_critic(state: ScriptState) -> Literal["writer", "human_review"]`.
A list or dict both work; the dict makes intent explicit. `END` is valid as a destination.

## Compile, config, recursion limit
```python
app = builder.compile(checkpointer=saver)
config = {
    "configurable": {"thread_id": thread_id},
    "recursion_limit": 40,
    "run_name": "script-pipeline",
    "tags": ["long"],
    "metadata": {"thread_id": thread_id},
}
```

## Human-in-the-loop
```python
def human_review(state: ScriptState) -> dict[str, Any]:
    decision = interrupt({"title": state["draft"].title, "score": state["critique"].overall})
    d = HumanDecision.model_validate(decision)
    return {"approved": d.approved, "feedback": d.feedback}
```
- First run returns `result["__interrupt__"] == (Interrupt(value=..., id=...),)`.
- From a checkpoint: `snap = await app.aget_state(config)`; `snap.next == ("human_review",)`;
  `snap.interrupts` holds the `Interrupt` objects; `snap.values` is the state.
- Resume: `await app.ainvoke(Command(resume={"approved": False, "feedback": "..."}), config, context=deps)`.
- **The node re-runs from its first line on resume.** Nothing with side effects before `interrupt()`.

## Streaming
```python
async for chunk in app.astream(inputs, config, context=deps, stream_mode="updates"):
    # {"planner": {"outline": ...}}  then ...  {"__interrupt__": (Interrupt(...),)}
    for node, update in chunk.items():
        ...
```

## Checkpointers + serde allowlist
Unregistered Pydantic types in state warn on load today and will be blocked later:
`Deserializing unregistered type ... Set LANGGRAPH_STRICT_MSGPACK=true ...`.
```python
serde = JsonPlusSerializer(allowed_msgpack_modules=[("proofline.schemas", "Outline"), ...])

saver = InMemorySaver(serde=serde)

conn = await aiosqlite.connect(".data/checkpoints.db")  # from_conn_string takes no serde
saver = AsyncSqliteSaver(conn, serde=serde)  # ... await conn.close() on shutdown

async with AsyncPostgresSaver.from_conn_string(url, serde=serde) as saver:
    await saver.setup()  # first time: creates tables
```
Verify with `LANGGRAPH_STRICT_MSGPACK=true` in a test: a load that would warn now fails.

## History / time travel
```python
async for snap in app.aget_state_history(config):  # newest first
    snap.values, snap.next, snap.config["configurable"]["checkpoint_id"]
```

## Mermaid
```python
print(app.get_graph().draw_mermaid())
```

## Pitfalls checklist
1. Side effects before `interrupt()` → run twice.
2. New state model not in the serde allowlist.
3. Accumulating list without reducer (silently overwritten).
4. Router reading settings or doing I/O.
5. Forgetting `context=` on resume → `runtime.context` is None.
6. Same `thread_id` for a new run → you resume the old one.
7. Unbounded `asyncio.gather` in the researcher → rate limits; use a semaphore.
