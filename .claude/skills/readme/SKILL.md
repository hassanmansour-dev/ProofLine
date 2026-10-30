---
name: readme
description: Write or refresh README.md as a portfolio-grade project page (Week 4) — problem, diagram, decisions, how to run, numbers, what's next.
disable-model-invocation: true
---

Write `README.md` for someone deciding in 60 seconds whether Hassan knows
LangGraph in production terms — a hiring manager or a senior engineer.

Read first: `docs/ARCHITECTURE.md`, all accepted ADRs, `docs/run-report.md`,
`docs/run-log.md`, `docs/retro-phase1.md` if present, and the actual CLI/API.

Structure:
1. One-line what + why (no adjectives).
2. Demo: a CLI stream excerpt or GIF placeholder + link to the video.
3. Graph (mermaid from `uv run python -m proofline.graph --mermaid`).
4. Design decisions — 5–7 bullets, each "decision → why", linking its ADR.
5. Numbers from real runs: avg revisions, score trajectory, latency, cost per script.
   Only measured numbers; if missing, leave a TODO, never estimate.
6. Run it: prerequisites, `.env`, CLI, API, tests.
7. Project layout (short).
8. What's next (Phase 2/3 bullets).

Keep it under ~200 lines. Show the draft to Hassan before overwriting an existing README.
