# ADR-0013: Measured Ops is Proofline's v1 scope; plan by order, not dates

- Status: accepted (2026-11-07, recording Hassan's decision of 2026-11-03 in Jira GROW-69)
- Date: 2026-11-07
- Phase/Step: Phase 1 / C1

## Context
The repo started as a 13-week, week-by-week plan for a single content pipeline,
with a public launch at the end and the repo private until then. Week 0 and Week 1
were built (ADR-0011, ADR-0012).

Hassan then decided to build Measured Ops: a LangGraph supervisor with specialist
agents that runs his work plan, with human approval for anything public, any
production deploy, any spend and any message to a real person. Building it as a
second project would split the flagship time budget in two.

He also set a planning rule: no due dates. His available time changes week to
week, so plans are ordered steps with dependencies.

## Decision
- **Measured Ops is Proofline v1.** The content pipeline is its first slice: the
  content agent, the eval/guard node and the approval inbox. Jira epic GROW-69
  replaces GROW-1; the old week tickets are closed as duplicates of the Measured
  Ops steps.
- **Two repos.** `proofline` (public) holds contracts, the orchestrator, agents
  with a demo config and sample data, the inbox and `measured_evals`.
  `measured-ops-private` holds real config, tuned prompts, personal calendar data,
  prospect lists, learner data and traces with personal data, and depends on
  `proofline` as a package.
- **The repo stays public while it is built.** The full history was checked on
  2026-11-07 (no secrets, no `.env`, no workplace references). The check is
  repeated before the launch tag.
- **`docs/PLAN.md` is ordered steps C0–C11 plus evals and launch**, each with its
  Jira ticket and what it waits on, and no dates. The `◀ CURRENT` marker sits on a
  step heading.
- **Existing code is kept.** The Week 1 nodes seed the content agent (C8); the
  critic seeds the eval/guard rubric (C7); the two-package layout from ADR-0012 is
  unchanged.

## Consequences
- The Phase 1 rule "no supervisor, no multi-agent, no MCP" from the earlier plan no
  longer holds: C2 introduces the supervisor and C5 MCP tools. Each step's scope
  in `docs/PLAN.md` is the limit instead.
- Where Measured Ops code lives inside this workspace is left to ADR-0014 (step C1).
- Public-repo discipline applies from now on, not from launch: every commit is
  written as public.

## Alternatives considered
- **Separate `measured-ops` repo next to Proofline.** Rejected: two flagships, half
  the time each, and the eval package would be consumed by two apps before it is stable.
- **Make the repo private until launch, as first planned.** Not chosen: the
  build-in-public posts link to the repo, and the private repo carries everything
  that must not be public.
