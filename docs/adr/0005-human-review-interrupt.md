# ADR-0005: Human review with `interrupt()` in a dedicated tiny node

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W3

## Context
The human must approve or reject with feedback, possibly hours later, from the
CLI or the API. LangGraph offers `interrupt()` (dynamic) and
`interrupt_before` (static breakpoints).

## Decision
- A dedicated `human_review` node calls `interrupt(payload)` and returns
  `{approved, feedback, history}` from the resume value.
- Resume with `Command(resume=HumanDecision(...).model_dump())`.
- The node does nothing before `interrupt()` except build the payload from
  state — because on resume LangGraph **re-runs the node from the top**.
- Requires a checkpointer; the API and CLI always run with one.

## Alternatives considered
- **`interrupt_before=["finalizer"]` + `update_state`** — works, but the review
  payload and the decision are implicit; harder to read and to test.
- **Approval outside the graph** — loses the loop (reject → writer) as a graph edge.

## Consequences
- Good: the review loop is a real, visible edge; resume is one call.
- Bad: any future side effect added before `interrupt()` would run twice.
  Code review must check this.
