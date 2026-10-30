# ADR-0002: TypedDict graph state, Pydantic values at every LLM boundary

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W1

## Context
LangGraph accepts TypedDict, dataclass, or Pydantic state. LLM outputs need
validation; graph state needs cheap partial updates and reducers.

## Decision
- `ScriptState` is a `TypedDict(total=False)`; nodes return partial dicts.
- Every value produced by an LLM is a Pydantic v2 model from `schemas.py`,
  obtained via structured output, and stored in state as that model.
- Derived numbers (word count, duration, critic overall) are computed
  properties on the models, never LLM-reported.

## Alternatives considered
- **Pydantic BaseModel as graph state** — validates on every update and makes
  partial updates and reducers clunkier; errors surface far from their cause.
- **Plain dicts everywhere** — fast to start, no contract, silent drift.

## Consequences
- Good: validation happens where the uncertainty is (LLM output); state stays simple.
- Bad: every Pydantic type in state must be registered with the checkpointer
  serializer (see ADR-0006).
- Revisit when: never likely; this is the standard shape.
