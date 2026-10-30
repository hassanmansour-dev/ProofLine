# ADR-0001: Explicit StateGraph, no prebuilt agents in Phase 1

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W1

## Context
The goal of Phase 1 is LangGraph fundamentals: state, nodes, edges, loops,
interrupts, checkpointing. Prebuilt agents (`create_react_agent` and friends)
and multi-agent patterns hide exactly those mechanics.

## Decision
Build a hand-wired `StateGraph` with six named nodes and two conditional
edges. No tool-calling agent loop, no supervisor, no MCP inside the app.
The researcher is a deterministic node that calls a search client.

## Alternatives considered
- **Prebuilt ReAct agent for research** — less code, but the LLM decides control
  flow; harder to test, and it skips the lesson. Phase 2.
- **Plain Python / LCEL chain** — no checkpointing, no interrupt, no loop
  semantics. Not the skill being built.

## Consequences
- Good: every transition is visible, testable, and drawn by `--mermaid`.
- Bad: research quality is capped by fixed queries from the planner.
- Revisit when: Phase 2 starts (researcher → supervisor + specialist agents).
  Rule carried forward: no agent where a fixed edge works.
