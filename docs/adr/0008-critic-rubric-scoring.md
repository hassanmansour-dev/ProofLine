# ADR-0008: Critic scores per criterion with evidence; overall computed in Python with an accuracy gate

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W2

## Context
A single LLM "score out of 10" drifts, clusters at 7–8, and can't be calibrated.
The loop threshold (8.0) only means something if the score is stable.

## Decision
- Rubric in `docs/RUBRIC.md`: 8 criteria, integer 1–10, anchored descriptions
  at 3 / 6 / 8 / 10.
- The critic returns, per criterion: score, a **verbatim quote** from the draft
  as evidence, and a concrete fix. Plus a ranked `must_fix` list (max 5).
- `Critique.overall` = weighted mean (weights in `schemas.py`), rounded to 1 dp,
  **capped at 6.0 if `technical_accuracy < 6`** (an inaccurate script never
  reaches the human as "good").
- Critic temperature 0; separate model role so it can be a different model later.

## Alternatives considered
- **LLM-reported overall** — uncalibrated, gameable by the writer's style.
- **Pairwise comparison with previous draft** — better signal, more cost; Phase 3 eval idea.

## Consequences
- Good: scores are explainable; the writer gets actionable fixes; Phase 3 can
  measure critic agreement with Hassan's judgment per criterion.
- Bad: more tokens per critique.
