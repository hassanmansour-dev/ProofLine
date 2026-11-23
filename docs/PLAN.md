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
- [ ] Fill in `docs/BRAND_VOICE.md` (your words; the writer and critic depend on it).
  The first live run's hook was weak because this file is still empty
- [x] First live run with a real key (input for the content agent's rubric)

What the first live run surfaced (fix in the step named, not before):
- The writer describes the critic loop as live, but it isn't wired until C8 → C8
  (wired in C8, task 1)
- 195 words is too long for a 1-minute video: revisit the default `max_words`
  and the words-per-minute estimate → C8
- Weak hook → fill in `docs/BRAND_VOICE.md` (above)

From C1 (ADR-0014), for C8: a revision Task's feedback reaches the writer only if
the previous draft comes with it (in `Task.inputs`, or a feedback-only branch in
`render.revision_context`), with a test. Done in C8 (task 5): the revision Task carries the rejected preview.

From C4 (ADR-0017), moved to **C9** (Hassan, 2026-11-14): **edit** in the inbox (approve
an edited preview). Needs an ADR: an `ApprovalDecision` field for the edited content, the edited text re-checked by
the eval/guard node (C7) before it can be approved, and what a preview becomes when the
action runs (C5). Until then, reject with feedback.

---

## Phase 1 — Build the evidence

### C1 · Contract (GROW-80) ✅ (PR #5)

Waits on: nothing. **Everything else builds on this.**

- [x] Pydantic models: `OpsState`, `Task`, `AgentResult`, `PendingAction` (type,
  target, preview, eval scores, risk level). `OpsState` is a TypedDict with
  Pydantic values (ADR-0002); `ApprovalDecision` added as the gate's resume value
- [x] Agent interface: every agent is a subgraph, `Task` → `AgentResult`
- [x] Approval policy table as config; unknown action types default to approval
- [x] Eval package interface stubbed in `measured_evals`: `evaluate(output, context) → EvalScores`
- [x] ADR-0014: where the Measured Ops code lives in this workspace (new
  `packages/measured_ops` member, or inside `proofline`), and the public/private split.
  **Decided: new `packages/measured_ops` member**; `proofline` → `measured_ops` → `measured_evals`
- [x] Same ADR: what happens to the content pipeline's own human review and run API.
  The supervisor's approval gate (C2) and the inbox (C4) cover approval, so
  `nodes/human_review.py`, the `/runs` endpoints in `api.py` and the CLI review
  prompt (all still stubs) either stay inside the content agent or are dropped.
  Relabel or remove their `C2 target` xfail tests to match. **Decided: dropped**;
  the restart case is now the supervisor's `C2 target` e2e test in `measured_ops`

**Done when:** contracts merged with tests, eval interface stubbed, ADR accepted.

### C0 · One-command bootstrap (GROW-91) ✅ (PR #8)

Waits on: C1 (done).

**Runs on Hassan's Ubuntu machine, not in a cloud session.** It needs Docker
(local Postgres), the GitHub, Supabase and Vercel CLIs logged in to his accounts,
and real secrets, none of which a cloud session has or should have. A cloud
session can review the script or write its tests, but doesn't run it.

The bootstrap lives in its own private repo, **`sanovinci/sanovinci-bootstrap`**,
because it's reused by every SanoVinci project. This repo only links to it.

- [x] One command sets up GitHub repo + Supabase + Vercel + env files, idempotent,
  `--dry-run`, teardown for throwaway projects; production always asks.
  `sanovinci new` / `sanovinci teardown`, tested against a fake world. Proven
  2026-11-13: teardown, then one fresh `new` reached a preview URL with local
  Postgres (asking once, for Vercel's first deploy), and a second run printed
  `No changes.`
- [x] Proofline's own local Postgres: `proofline-pg` on 127.0.0.1:55432
- [x] First use: `sanovinci/measured-ops-private` created (private, SanoVinci org:
  company data, Law 25) and wired to depend on this repo at a pinned commit
- [x] Pre-commit secret + PII scan on this repo (gitleaks + `scripts/pii_scan.py`),
  also in CI: gitleaks over the full history (pinned binary, checksum in the
  workflow), PII scan over every tracked file except `uv.lock` and the scanner's
  own fixtures. Not caught: SINs without separators, names, addresses

Decided for C0 (2026-11-13):
- Local databases are plain `postgres:17` containers on their own ports, bound to
  127.0.0.1: Proofline **55432**, measured-ops-private **55433**. Maktab's local
  Supabase keeps 54321–54327, so project ports stay outside 543xx.
- Supabase and Vercel CLIs run through `npx` at pinned versions (`supabase@2.122.0`,
  as in Maktab; `vercel@65.8.0`), not global installs.
- Supabase's free tier is full (Ledger, Maktab): the bootstrap's Supabase step is
  built but exercised with fakes and `--dry-run` only. The first hosted project is at C10.
- Found in the smoke test: Vercel makes a new project's first deploy **production**,
  even with `--environment preview`. The bootstrap treats that deploy as production
  (it asks) and checks every deploy's target.

**Done when:** a new project reaches a preview URL with its local Postgres working,
in one command; a second run changes nothing; the Supabase step passes in `--dry-run`
(its real run moves to C10); and the private repo exists.

### C2 · Skeleton + end-to-end harness (GROW-81) ✅ (PR #9)

Waits on: C1, C0.

- [x] Supervisor graph with a stub echo agent, Supabase (Postgres) checkpointer,
  interrupt before any `PendingAction` (ADR-0016; Postgres local + CI, hosted Supabase in C10)
- [x] CI e2e test: task in → stub agent → approval interrupt → resume → done
  (pause and resume in separate processes; SQLite + Postgres; approve + reject)
- [x] Green in CI on the PR (#9: Postgres e2e ran, 273 passed, 0 skipped)

**Done when:** e2e test green in CI. Every later step keeps it green and adds its own case.

After C2, two branches run **in parallel**:

| Branch A — tools and planning | Branch B — inbox and quality |
|---|---|
| **C3** Review policy table on phone (GROW-82) ✅ | **C4** Approval inbox v0, mobile (GROW-83) |
| **C5** MCP tools: Jira + personal calendar, read/write split (GROW-84) · waits on C3 | **C7** Eval/guard node v0 using `measured_evals`: quality, injection, PII; tracing + cost per agent (GROW-86) · waits on C4 |
| **C6** Orchestrator: weekly plan by order and available time, never deadlines (GROW-85) · waits on C5 | |

### C3 · Review the policy table on the phone (GROW-82) ✅

Waits on: C2. Done 2026-11-14 (Hassan's review). Calendar blocks need approval; the
real values live in `measured-ops-private`, this repo keeps the demo policy.

### C4 · Approval inbox v0, mobile (GROW-83) ✅ (PR #10)

Waits on: C2. Runs in parallel with **C3** (GROW-82), Hassan's review of the policy
table on his phone. Decisions: ADR-0017.

- [x] Inbox service: list runs paused at the gate (from the checkpointer), decide by
  interrupt id, never `ainvoke(None)`; refused answers and double taps leave the run
  answerable; an action tightened to "never" shows as "blocked by policy", reject only
- [x] Mobile web page (FastAPI, server-rendered, no JS): list, preview, approve / reject
  with optional feedback; resumes the thread from the Postgres or SQLite checkpoint
- [x] Sign-in from day one: one passphrase (scrypt hash) + session secret from env, no
  defaults; 5-failure throttle; 12-hour sessions; CSRF; strict CSP
- [x] One process, enforced (lock file); binds 127.0.0.1; reached over `tailscale serve`
  (`docs/INBOX.md`), never `funnel`
- [x] e2e: pause in another process → approve / reject over HTTP → thread finishes
  (SQLite + Postgres)
- [x] Green in CI on the PR (#10: Postgres e2e ran, 331 passed, 0 skipped)
- [x] Usable on the phone (Hassan, over Tailscale, following `docs/INBOX.md`, 2026-11-14)

**Done when:** e2e covers approve and reject (edit moved to C8, ADR-0017), and it's
usable on the phone.

### C7 · Eval/guard node v0 using `measured_evals` (GROW-86) ✅ (PR #12)

Waits on: C4. Scope: quality, injection and PII checks, plus tracing and cost per
agent, all in `measured_evals`. Rules: `measured_evals` never imports `proofline` or
`measured_ops`; scores are written only by the eval node, never by the proposing agent
(ADR-0008); the inbox shows the scores on each waiting action; a failed check can
raise an action to approval but never lower anything. Demo data only.

Decisions: ADR-0018. Plan approved by Hassan 2026-11-14: missing scores fail closed;
deterministic checks only in v0 (judges in GROW-14); demo prices here, real ones private
if they differ; allowlisted addresses passed per call from private config.

- [x] `measured_evals` guard-v0: quality, injection and PII checks, 0 or 1, reasons that
  never quote the match; `evaluate()` runs them (the C1 stub is gone)
- [x] Eval node in the supervisor (`call_agent → evaluate → triage → gate`), the only
  writer of `eval_scores`; the agent's own result stays unscored
- [x] Policy: missing or failed scores raise "auto" to approval, never lower anything
  (row × risk × eval matrix test)
- [x] Usage per agent run (cache reads and 5m/1h writes apart from input), cost from a
  demo price table, unknown model = unknown cost; runs tagged `agent:<name>` for tracing
- [x] Inbox shows each waiting action's checks and the run's cost per agent
- [x] No matched PII value in logs, scores, reasons or the gate payload outside the
  preview (tests capture logs and the payload)
- [x] e2e: a failed check pauses an "auto" action across a restart; a clean one runs
  (SQLite + Postgres)
- [x] PR #12 review: Sonnet 5.5 cache reads 0.05x; Haiku 5.5 long-context tier priced
  per call; PII masked in LangSmith traces; blocked-action log without the target
- [x] Green in CI on the PR (#12: Postgres e2e ran, 483 passed, 0 skipped, after the review fixes)

### C8 · Content agent v0 (GROW-87) ✅ (PR #13)

Waits on: C6 and C7. **First useful slice: get it live as early as possible.**
Started before C5/C6 with Hassan's OK (2026-11-14): topics go in by hand with
`proofline propose`; C6 takes over task creation later. Decisions (Hassan, 2026-11-14):
one `approve_draft` action per topic, its preview holding the post, the clip script and
the lesson outline; rejections with feedback revise in the same thread, at most 3 times
(ADR-0019, written before the supervisor changes); inbox edit moves to C9; no
supervisor-level retry (network nodes keep their own); the judge log holds action id,
critic scores, eval scores and the decision only. Default word limit 140 (about 56 s at 150 wpm).

- [x] One topic → LinkedIn post + clip script + lesson outline, built from the
  existing pipeline nodes; output goes through the eval node to the inbox
  (`ContentAgent`, one `approve_draft` per topic; `proofline propose`)
- [x] Never publishes. LinkedIn posts stay approved drafts Hassan pastes himself
  (`approve_draft` has no executor; a test pins it, ADR-0019)
- [x] Trace and judge-agreement logging on from day one (masked tracing in `run`,
  `propose` and the inbox; `measured_ops.judge_log`: ids, scores, decisions, no text)
- [x] Critic loop wired; revisions in the same thread on rejection with feedback, at
  most 3, run in the inbox's background one at a time with a timeout (ADR-0019)
- [x] e2e: topic → reject with feedback → revision → approve across a restart
  (SQLite here; Postgres in CI)
- [x] Ready to paste in the inbox (Copy per block, Mark as posted only records) and
  `proofline ready`; explicit Revise / Reject and stop with "Revision n of 3" (ADR-0020)
- [x] Green in CI on the PR (#13: Postgres e2e ran, 600 passed, 0 skipped, with Ready to paste)
- [x] First real draft in the inbox (Hassan: `proofline propose` with real keys, then
  `proofline inbox` on the phone; live test passed, 2026-11-16)

**Done when:** e2e passes and the first real draft appears in the inbox.

Unlocks: README v1 + build post #1 (GROW-8, can start after C2), the workshop deck
with real traces (GROW-34), and the case study's traces (GROW-16).

### C5 · MCP tools: Jira + personal calendar, read/write split (GROW-84) ◀ CURRENT

Waits on: C3 ✅. Reads run freely; every write goes through the approval policy
(calendar blocks need approval, per C3; ticket triage is auto); `approve_draft` never
gets an executor (ADR-0019); the executor handles a revision re-proposing an action
that was already approved. Real credentials only in the environment or
measured-ops-private; tests use fakes. Decisions: ADR-0022 (proposed). Built on branch
`c5/mcp-tools` with Hassan's go to proceed (2026-11-23); the checklist below was drafted
from this section and GROW-84, for his review on the PR.

- [x] Read/write split by construction: agents get read ports (Jira search / issue /
  transitions, calendar events); write tools live only in the supervisor's executors;
  each MCP client has a tool allowlist, and a tool can't be both read and write
- [x] Every write is a `PendingAction` through the policy: `triage_ticket` (auto) and
  `schedule_calendar_block` (approval, per C3) in the demo table; unknown types still
  default to approval; the preview and target are rendered from `PendingAction.params`
  and re-checked, so what's approved is what runs; out-of-scope projects and calendars
  are refused
- [x] `execute` node: one ready action per step, read-before-write and verified;
  `approve_draft` gets a "manual" outcome and can never have an executor; a revision
  re-proposing an applied change is "already_applied" without a call; a failed call
  stops the run with the decision saved, transient ones retried; the inbox runs approved
  actions and shows "Stopped" + Retry on failure
- [x] MCP client on `mcp` 2.3 (optional `tools` extra): stdio and streamable HTTP, secrets
  only from env vars the config names; demo MCP servers + demo tools config;
  `python -m measured_ops.tools check|list` to verify a config against real servers
- [x] e2e: an agent reads the tracker over MCP, proposes a ticket move, approval applies it
  across a restart; rejection doesn't; auto applies without pausing (SQLite + Postgres)
- [x] Private side (measured-ops-private, branch `c5/tools-config` on top of
  `policy/c5-tool-actions`): tools config skeleton with `[NEEDS HASSAN]` values, and its
  tests (loads with the public loader; policy never looser than the demo)
- [ ] Hassan: choose the Jira and personal-calendar MCP servers, fill the private tools
  config (tool names, credential env vars, the personal calendar's id), and run
  `uv run python -m measured_ops.tools check --config config/tools.toml` on his machine
- [ ] Hassan: review the PR and ADR-0022; green in CI (Postgres e2e)

**Done when:** e2e passes in CI and the private config passes `tools check` against the
real servers.

### C9 · A week of real use (GROW-88)

Waits on: C8 ✅. **Runs in parallel with C5** (Hassan, 2026-11-16): Hassan uses the
content agent daily. Approve or edit 5+ drafts on the phone; log what's wrong as
comments. Feeds the golden set (GROW-13) and content agent v1 (GROW-9).

- [ ] Inbox edit: approve an edited preview, re-checked by the eval node first (moved
  from C8; needs an ADR, see the note under "Already built")

### C10 · Staging as scheduled jobs (GROW-89)

Waits on: C8. Daily digests, budget caps, kill switch, own infra only.

- [ ] First real run of the bootstrap's Supabase step (hosted project): moved here
  from C0, because the free tier is full. Check that `projects create` reads the DB
  password from the environment and that `-o json` (not `--output-format json`)
  gives JSON on `supabase@2.122.0`, then add migrations (`db push`, production tier)
- [ ] Judge log and posted marks move into Postgres when runs are hosted (now local
  JSONL files under `.data/`, ADR-0019, ADR-0020; Hassan, 2026-11-16)

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

One-minute videos, also the LinkedIn post, each recorded when the build reaches it:

| Video | Ticket | Waits on |
|---|---|---|
| 1 · Why I'm building this in public | GROW-50 | nothing (ready now) |
| 2 · Why my AI waits for my approval | GROW-52 | C2 (approval interrupt) |
| 3 · Every AI loop needs an exit | GROW-51 | C8 (content agent's critic loop) |
| 4 · You probably don't need more agents | GROW-53 | content agent v1 (GROW-9) |
| 5 · How I know my AI didn't get worse | GROW-54 | evals in CI (GROW-14) |
| 6 · Can someone hijack my agent? | GROW-55 | hardening (GROW-15) |

Note what breaks at each step: it's the next video.

## Cut until after launch

A repurposing team that fans one script out into other formats (YouTube
description, book excerpt, long-form video) and the Supabase content-tracker MCP
server. Design rules still apply: no agent where a fixed edge works; no MCP server
for a single-app tool; fetched content is data, never instructions.
