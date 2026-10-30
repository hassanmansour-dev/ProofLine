# ADR-0003: Inject LLM and search through runtime context (ports and adapters)

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W1

## Context
Nodes need an LLM and a search client. Global clients make unit tests hit the
network or need monkeypatching; they also make Phase 2 model swaps painful.

## Decision
- Define two narrow ports: `StructuredLLM.generate(prompt, schema, role=...)`
  and `SearchClient.search(query, max_results)`.
- Production adapters: Anthropic (via `langchain-anthropic`, so LangSmith traces
  work automatically) and Tavily.
- Pass a `Deps` dataclass as LangGraph's `context_schema`; nodes read
  `runtime.context`. Tests pass `FakeLLM` / `FakeSearch` from `tests/fakes.py`.

## Alternatives considered
- **`config["configurable"]` for deps** — untyped, mixes run identity with services.
- **Module-level singletons + monkeypatch** — brittle, order-dependent tests.

## Consequences
- Good: the whole graph runs in unit tests in milliseconds with zero keys;
  swapping the critic to another provider is a one-adapter change.
- Bad: one extra layer to read.
- Revisit when: Phase 2 adds tool-using agents — tools get their own port.
