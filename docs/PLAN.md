# Plan — Proofline v1 = Measured Ops

**Order, not dates.** Hassan's available time changes week to week, so this plan
has no due dates: steps run in the order below, and a step starts when the steps
it depends on are done. Move the `◀ CURRENT` marker to the step being worked on;
the session-start hook and Claude Code read it to know the scope.

Jira: epic **GROW-69** on sanovinci.atlassian.net. Every step lists its ticket and
what it waits on. Dependencies are also set as "blocked by" links in Jira.

## Scope (ADR-0013)

**Measured Ops is Proofline's v1 scope, not a second project.** Measured Ops is a
LangGraph supervisor with specialist agents that runs Hassan's work plan, with a
human approving anything that has real impact. Proofline, the eval-gated content
pipeline, is its first slice: the content agent, the eval/guard node and the
approval inbox. One build, one flagship.

Two repos:

| Repo | Visibility | Holds |
|---|---|---|
| `proofline` (this one) | Public | Contracts, the orchestrator, agents with a **demo config and sample data**, the approval inbox, and `measured_evals` (published as open source) |
| `measured-ops-private` | Private (created in C0) | Real config and policy values, prompts tuned to real accounts, calendar (personal only, never work), prospect lists, learner data, traces with personal data. Depends on `proofline` as a package |

Nothing private is ever committed here. Pre-commit secret and PII scan on this repo.

## Already built (Week 0–1 of the earlier plan)

Kept and reused, not thrown away:

- [x] Repo on personal GitHub, uv workspace with two packages (ADR-0012):
  `packages/proofline` and a standalone, boundary-tested `packages/measured_evals`
- [x] Contracts locked for the content pipeline (ADR-0011), versioned prompts
- [x] Nodes: planner, researcher (stub + Tavily web), writer, critic, finalizer, with
  fake-model tests; linear graph; `proofline run --topic --audience`
- [x] Checkpointer factory and API module scaffolding; CI (ruff, mypy, pytest) on both packages
- [x] Pre-public check (2026-11-07): full history has no secrets, no `.env`, no
  workplace references. Re-run before launch (L2).

The content pipeline above becomes the seed of the **content agent** (C8). Its
critic becomes the first rubric in the eval/guard node (C7).

Open from the earlier Week 0–1:
- [ ] Fill in `docs/BRAND_VOICE.md` (your words; the writer and critic depend on it)
- [ ] First live run with a real key (input for the content agent's rubric)

---

## Phase 1 — Build the evidence

### C1 · Contract (GROW-80) ◀ CURRENT

Waits on: nothing. **Everything else builds on this.**

- [ ] Pydantic models: `OpsState`, `Task`, `AgentResult`, `PendingAction` (type,
  target, preview, eval scores, risk level)
- [ ] Agent interface: every agent is a subgraph, `Task` → `AgentResult`
- [ ] Approval policy table as config; unknown action types default to approval
- [ ] Eval package interface stubbed in `measured_evals`: `evaluate(output, context) → EvalScores`
- [ ] ADR-0014: where the Measured Ops code lives in this workspace (new
  `packages/measured_ops` member, or inside `proofline`), and the public/private split

**Done when:** contracts merged with tests, eval interface stubbed, ADR accepted.

### C0 · One-command bootstrap (GROW-91)

Waits on: C1.

- [ ] One command sets up GitHub repo + Supabase + Vercel + env files, idempotent,
  `--dry-run`, teardown for throwaway projects; production always asks
- [ ] First use: create `measured-ops-private` and wire it to depend on this repo
- [ ] Pre-commit secret + PII scan on this repo

**Done when:** a new project reaches a preview URL with working Supabase in one
command, a second run changes nothing, and the private repo exists.

### C2 · Skeleton + end-to-end harness (GROW-81)

Waits on: C1, C0.

- [ ] Supervisor graph with a stub echo agent, Supabase (Postgres) checkpointer,
  interrupt before any `PendingAction`
- [ ] CI e2e test: task in → stub agent → approval interrupt → resume → done

**Done when:** e2e test green in CI. Every later step keeps it green and adds its own case.

After C2, two branches run **in parallel**:

| Branch A — tools and planning | Branch B — inbox and quality |
|---|---|
| **C3** Review policy table on phone (GROW-82) | **C4** Approval inbox v0, mobile (GROW-83) |
| **C5** MCP tools: Jira + personal calendar, read/write split (GROW-84) · waits on C3 | **C7** Eval/guard node v0 using `measured_evals`: quality, injection, PII; tracing + cost per agent (GROW-86) · waits on C4 |
| **C6** Orchestrator: weekly plan by order and available time, never deadlines (GROW-85) · waits on C5 | |

### C8 · Content agent v0 (GROW-87)

Waits on: C6 and C7. **First useful slice: get it live as early as possible.**

- [ ] One topic → LinkedIn post + clip script + lesson outline, built from the
  existing pipeline nodes; output goes through the eval node to the inbox
- [ ] Never publishes. LinkedIn posts stay approved drafts Hassan pastes himself
- [ ] Trace and judge-agreement logging on from day one

**Done when:** e2e passes and the first real draft appears in the inbox.

Unlocks: README v1 + build post #1 (GROW-8, can start after C2), the workshop deck
with real traces (GROW-34), and the case study's traces (GROW-16).

### C9 · A week of real use (GROW-88)

Waits on: C8. Approve or edit 5+ drafts on the phone; log what's wrong as comments.
Feeds the golden set (GROW-13) and content agent v1 (GROW-9).

### C10 · Staging as scheduled jobs (GROW-89)

Waits on: C8. Daily digests, budget caps, kill switch, own infra only.
**Done when:** it runs unattended for 3 days with digests arriving.

### C11 · Remaining build agents, one at a time (GROW-90)

Waits on: C10. Same recipe each: contract → e2e case → eval thresholds → one
week of phone review → enable. Never two new agents at once. Order:

1. **Academy agent** (GROW-75) · also waits on the Maktab MCP server (GROW-93),
   built in the Maktab repo, which waits on the Academy institution (GROW-61)
2. **Proofline build agent + Measured Index agent** (GROW-77)
3. **Admin agent** (GROW-79)

### Evals and hardening

- **Content agent v1** (GROW-9) · waits on C9: parallel researchers, independent
  fact-checker, read-only knowledge MCP server
- **Golden set, 20 topics** (GROW-13) · waits on C9: seeded from real edits;
  doubles as Measured Lab's first exercises
- **DeepEval + promptfoo in CI, publish `measured-evals`** (GROW-14) · waits on GROW-13.
  Measured Lab v0 (GROW-68) and Measured Index #1 (GROW-67) wait on this
- **Production hardening** (GROW-15): cost budgets, injection suite, auth, rate limits

### Launch

- **L1 Case study + demo video** (GROW-16) · waits on C8 traces; demo uses the
  public demo config, no private data on screen
- **L2 Public launch** (GROW-17) · waits on L1 and GROW-14: re-run the pre-public
  check, choose a licence, tag `v1.0.0`, publish `measured-evals`, case study on
  hassanmansour.com, build post #3. **This gate opens Phase 2.**

## Phase 2 — Use the evidence (after L2)

Outreach agent (GROW-76) and the next waves (GROW-95 to GROW-98), each gated in Jira
on what it depends on.

## Build-in-public videos

One-minute videos, also the LinkedIn post, in this order (GROW-50 to GROW-55):
why I'm building this in public → every AI loop needs an exit → why my AI waits for
my approval → you probably don't need more agents → how I know my AI didn't get
worse → can someone hijack my agent? Note what breaks each step: it's the next video.

## Cut until after launch

A repurposing team that fans one script out into other formats (YouTube
description, book excerpt, long-form video) and the Supabase content-tracker MCP
server. Design rules still apply: no agent where a fixed edge works; no MCP server
for a single-app tool; fetched content is data, never instructions.
