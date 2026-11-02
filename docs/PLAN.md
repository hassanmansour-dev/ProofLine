# Plan — Proofline (13 weeks, Oct 26, 2026 → Jan 24, 2027)

Cadence: ~4 evening sessions per week, 60–90 min each (about 6 hrs/week).
Each session below is sized for one sitting. Move the `◀ CURRENT` marker as
you go; Claude Code reads it to know the scope. Jira: epic GROW-1 on
sanovinci.atlassian.net (one ticket per week, due the Sunday of that week).

| Phase | Weeks | Theme |
|---|---|---|
| 1 | 1–4 | LangGraph fundamentals with production habits |
| 2 | 5–8 | Multi-agent (supervisor, parallel researchers, fact-checker) + MCP servers |
| 3 | 9–11 | Evals, cost budgets, security — built in `packages/measured_evals` |
| Launch | 12–13 | Case study, demo video, repo public |

Build-in-public videos (1 min, every two weeks, also the LinkedIn post):
V1 Nov 8 · V2 Nov 22 · V3 Dec 6 · V4 Dec 20 · V5 Jan 3 · V6 Jan 17. Scripts
live in Jira GROW-50…55. Note what breaks each week: it's the next video.

Output: one short technical script (≤200 words) per topic, used as a LinkedIn post
and a 1-minute video. Two packages in one uv workspace (ADR-0012): `proofline` (the
pipeline app) and `measured_evals` (eval + security layer; never imports proofline;
open-sourced at launch, reused by the Measured Lab web app later).

Phase 1 exit criteria: a script on a real topic goes plan → research → write →
critic loop → you reject once with feedback → rewrite → you approve → final
Markdown with real sources, resumable after a process kill, callable via API,
traced in LangSmith, explained in a README.

---

## Week 0 — Setup (Oct 29–30, ~45 min)

- [x] Create repo `proofline` on your personal GitHub (private until launch), unzip this kit into it, first commit
- [ ] `uv sync`, copy `.env.example` → `.env`, add `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`
- [ ] Open Claude Code in the repo and paste the kickoff prompt from `docs/KICKOFF.md`
- [x] Fast suite runs: routing + schema tests pass, Week 1 targets show as `xfailed`
- [ ] Fill in `docs/BRAND_VOICE.md` (15 min, your words — the writer and critic depend on it)

**Done when:** `uv run pytest -m "not live"` is green with xfails, and Claude Code
session start shows "Week 0".

---

## Week 1 — Linear end to end (Oct 26 → Nov 1 · GROW-5) ◀ CURRENT

Goal: one straight pass works on a real topic. No loops yet.

- [x] **S1 Contracts.** Review the kit's contracts with the `langgraph-architect` and
  `prompt-engineer` agents; lock them via ADR-0011 (open items deferred to Week 3).
- [x] **S2–S4 Nodes.** planner, researcher, writer, critic (built, wired in Week 2) and
  finalizer, each with FakeLLM / FakeSearch tests.
- [x] **S5 Brief reset (ADR-0012).** uv workspace: `packages/proofline` + empty
  `packages/measured_evals` (boundary-tested). State per the brief (topic, audience,
  research_notes, draft, critique, score, revision_count, approved, feedback). One
  short format (≤200 words, title/hook/body/CTA). Brand name from
  `PROOFLINE_BRAND_NAME`. `Researcher` protocol with `StubResearcher` (default) and
  `WebResearcher` (Tavily). Model factory from env. Linear graph
  planner → researcher → writer → finalizer; `proofline run --topic --audience`;
  end-to-end tests with a fake model; CI on both packages; README.
- [ ] **S6 First live run.** Add `ANTHROPIC_API_KEY` to `.env`, then
  `uv run proofline run --topic "Why every AI loop needs an exit" --audience "developers new to AI"`.
  Read the output critically and note 3 things a critic should catch (input for Week 2).

**Done when:** that command prints a finished script; `uv run pytest` passes for both
packages (node tests + an end-to-end graph test with a fake model); ruff and mypy are
clean in CI; the README explains the two-package design and the roadmap.

**Concepts you should be able to explain after this week:** StateGraph, partial
updates, `START`/`END`, runtime context, structured output, ports and adapters
(Researcher, StructuredLLM), why ids are assigned in code, why the eval package
can't import the app.

---

## Week 2 — Critic loop + guard (Nov 2 → 8 · GROW-6)

Goal: the graph revises itself until good enough or out of budget.

- [ ] **S1 Routing.** Wire the critic after the writer and `route_after_critic` (reads
  `score`) with a path map. Table-driven tests already exist in
  `packages/proofline/tests/test_routing.py` — extend for edge cases (exact threshold, gate).
- [ ] **S2 Writer revision mode.** Revision prompt path: must_fix first, keep what scored
  well, increment `revision_count`, append `history`. Test: second pass sees the critique.
- [ ] **S3 Stream it.** CLI uses `astream(stream_mode="updates")` to print each node and
  score as it happens. Watch a loop live.
- [ ] **S4 Calibrate the critic.** Run 3 topics. For each final draft, score it yourself on
  the rubric *before* reading the critic's scores. Note per-criterion gaps in
  `docs/calibration.md`. Adjust the critic prompt (bump version + changelog). This
  is the seed of Phase 3's "critic vs your judgment" eval.

- [ ] **Video 1** (record Sat Nov 7, post Sun Nov 8): "Why I'm building Proofline in public" (GROW-50).

**Done when:** a loop of ≥2 revisions is visible in the CLI stream; guard tests prove the
loop ends at the threshold and at `max_revisions`; recursion limit never hit.

**Concepts:** conditional edges, path maps, cycles, reducers (`history`), recursion limit,
stream modes.

---

## Week 3 — Interrupt, checkpointer, FastAPI (Nov 9 → 15 · GROW-7)

Goal: a human is in the loop, runs survive restarts, there's an API.

- [ ] **S1 Human review.** `nodes/human_review.py` with `interrupt()`; `route_after_human`.
  CLI prompts approve/reject + feedback and resumes with `Command(resume=...)`.
  Test with `InMemorySaver`: reject → writer → critic → review → approve → finalizer.
- [ ] **S2 Checkpointer.** `checkpointing.py` memory + SQLite with the serde allowlist.
  Test: run to interrupt, close saver, reopen, resume with same `thread_id` → completes.
  `proofline show THREAD_ID` prints history from the checkpoint.
- [ ] **S3 API.** `api.py` `create_app()`: lifespan opens the checkpointer + compiles once;
  `POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/review`. `tests/test_api.py` is the contract.
- [ ] **S4 Stretch: Supabase Postgres.** `AsyncPostgresSaver` behind the same factory;
  run the resume test against it. (Skip without guilt if the week ran long.)

**Done when:** kill the process at the review step, restart, resume via API, get the
final script.

**Concepts:** `interrupt` vs breakpoints, node re-execution on resume, `Command`,
thread_id, checkpoint history / time travel (`aget_state_history`).

---

## Week 4 — Tracing, README, build post #1 (Nov 16 → 22 · GROW-8)

Goal: it's observable, explainable, and published.

- [ ] **S1 Tracing.** LangSmith env, run metadata (thread_id, prompt versions, models),
  tags. Run one full flow with a reject. In LangSmith: find the slowest node, the most
  expensive call, total tokens/cost. Write the numbers in `docs/run-report.md`.
- [ ] **S2 README.** Use `/readme`: problem, diagram, design decisions (link ADRs), how to
  run, what's next. Screenshots of the trace and the CLI stream.
- [ ] **S3 Video 2.** "Every AI loop needs an exit" (GROW-51, post Sun Nov 22). Bonus:
  generate the script *with Proofline* and approve it through the API before recording.
  Doubles as LinkedIn build post #1.
- [ ] **S4 Retro + tag.** `docs/retro-phase1.md`: what worked, what was hard, what
  Phase 2 must change. Tag `v0.1.0`.

**Done when:** README + Video 2 published; `v0.1.0` tagged. (Day-30 checkpoint.)

---

## Week 5 — Supervisor + web researcher (Nov 23 → 29 · GROW-9)

Goal: research becomes multi-agent without changing the `ResearchNotes` contract.

- [ ] **S1 ADR.** Supervisor design: what it decides (which researchers a topic needs),
  state it adds, how results merge into `ResearchNotes`. Revisit ADR-0001 and ADR-0003.
- [ ] **S2 Web researcher agent.** A tool-using agent replaces `StubResearcher` behind the
  existing `Researcher` protocol (graph.py unchanged); same untrusted-data rules (ADR-0007).
- [ ] **S3 Supervisor.** Routes by topic type; routing stays pure and tested.
- [ ] **S4 Tests + live run.** Writer and critic unchanged; contract tests still green.

**Done when:** the supervisor routes research by topic type and downstream nodes didn't change.

## Week 6 — GitHub/docs researcher, parallel fan-out (Nov 30 → Dec 6 · GROW-10)

- [ ] **S1** GitHub/docs researcher agent (own port, fakes first).
- [ ] **S2** Parallel fan-out (`Send` or parallel edges) + reducer merge; dedupe across researchers.
- [ ] **S3** Failure isolation: one researcher failing doesn't kill the run.
- [ ] **Video 3** "Why my AI waits for my approval" (GROW-52, post Sun Dec 6).

**Done when:** researchers run in parallel and results merge with stable `S1..Sn` ids.

## Week 7 — Independent fact-checker (Dec 7 → 13 · GROW-11)

Lighter week: the pilot workshop is this week.

- [ ] **S1 ADR.** Fact-checker placement (before or beside the critic); different model from the writer.
- [ ] **S2** Fact-checker: each claim → supported / unsupported / contradicted, with source ids.
- [ ] **S3** Unsupported claims feed the writer's `must_fix` and the critic's accuracy gate.

**Done when:** unsupported claims are flagged with source gaps and the writer fixes them.

## Week 8 — MCP servers: read-only knowledge (Dec 14 → 20 · GROW-12)

- [ ] **S1 ADR.** Server scope: read-only tools over past scripts + book chapters. Least
  privilege, no write tools.
- [ ] **S2** Build the server as its own workspace member (e.g. `packages/knowledge_mcp/`);
  usable from Claude Desktop/Code too.
- [ ] **S3** Pipeline loads tools through an MCP client; planner/writer use past scripts to
  avoid repeats and keep the voice.
- [ ] **Video 4** "You probably don't need more agents" (GROW-53, post Sun Dec 20).
  Doubles as LinkedIn build post #2.

**Done when:** the pipeline pulls knowledge via MCP. Tag `v0.2.0`. (Day-60 checkpoint.)

## Week 9 — Golden set (Dec 21 → 27 · GROW-13) — holiday-light

- [ ] Golden-set format and loader in `packages/measured_evals` (app-agnostic); Proofline's
  20 topics in `packages/proofline/evals/golden/`, each with audience and what a good
  script must show (must-mention points, must-not-claim, word limit).

**Done when:** 20 topics with expected qualities are committed.

## Week 10 — Evals in CI (Dec 28 → Jan 3 · GROW-14)

- [ ] **S1** DeepEval metrics in `measured_evals`: faithfulness to sources, rubric score, cost, latency.
- [ ] **S2** promptfoo config for prompt/model regression on the golden set (Proofline runs
  it through `measured_evals`).
- [ ] **S3** CI (GitHub Actions): eval job on PRs that touch prompts or models; fail on regression.
- [ ] **S4** Critic vs your judgment, using `docs/calibration.md`.
- [ ] **Video 5** "How I know my AI didn't get worse" (GROW-54, post Sun Jan 3).

**Done when:** CI fails on a deliberate regression and passes after the fix.

## Week 11 — Production hardening (Jan 4 → 10 · GROW-15)

- [ ] **S1** Per-run cost budget (abort when over) + loop/failure alerts; the budget
  primitive lives in `measured_evals`.
- [ ] **S2** Prompt-injection suite in `measured_evals`: pages with hidden instructions;
  assert Proofline ignores them.
- [ ] **S3** FastAPI auth (API key) + rate limiting; secrets only from env.
- [ ] **S4** Output checks before finalize (citations resolve, length, banned claims).

**Done when:** injection tests pass and a run aborts over budget. Tag `v0.3.0`.

## Week 12 — Case study + demo video (Jan 11 → 17 · GROW-16)

- [ ] Case study: problem, architecture diagram, eval table, cost per run, what failed and how you fixed it.
- [ ] Demo video (~3 min): one topic from input to approved script.
- [ ] Final README pass (`/readme`).
- [ ] **Video 6** "Can someone hijack my agent?" (GROW-55, post Sun Jan 17).

**Done when:** case study draft reviewed and demo video recorded.

## Week 13 — Public launch (Jan 18 → 24 · GROW-17)

- [ ] Pre-public check: no secrets in git history, no workplace references, `.env.example` clean, licence chosen.
- [ ] Make the repo public. Tag `v1.0.0`. Publish `measured-evals` to PyPI (its own README,
  licence and version).
- [ ] Case study live on hassanmansour.com; demo video posted; LinkedIn launch post (#3); pin to Featured.

**Done when:** repo public, case study live, video posted. (Day 90.)

---

## Cut until after launch

A repurposing team that fans one script out into other formats (YouTube description,
book excerpt, long-form video) and the Supabase content-tracker MCP server. The short
script itself is the LinkedIn post and the 1-minute video; that's the core output, not
a repurposing step. Design rules still apply: no agent where a
fixed edge works; no MCP server for a single-app tool; fetched content is data,
never instructions.
