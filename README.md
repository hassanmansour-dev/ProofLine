# Proofline

**A multi-agent content pipeline where nothing ships without passing the evals
and a human sign-off.**

Proofline turns a topic into an approved, sourced video script for Build
Clearly: plan → research → write → critique loop → human review → finalize.

> Built in public, Oct 2026 → Jan 2027. The full README is written in Week 4
> (`/readme` in Claude Code). Until then, start here:

- Plan and current week: [`docs/PLAN.md`](docs/PLAN.md)
- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Decisions: [`docs/adr/`](docs/adr/)
- Getting started with Claude Code: [`docs/KICKOFF.md`](docs/KICKOFF.md)
- Claude Project setup: [`docs/PROJECT_INSTRUCTIONS.md`](docs/PROJECT_INSTRUCTIONS.md)

```bash
uv sync
cp .env.example .env   # add ANTHROPIC_API_KEY, TAVILY_API_KEY
uv run pytest -m "not live"
```
