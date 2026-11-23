# Case study: Proofline, an eval-gated agent system with a human at the gate

Hassan Mansour, Senior Applied AI Developer · repository state at commit
[`8fdc21c`](https://github.com/hassanmansour-dev/ProofLine/commit/8fdc21ce2e02ae1910376a41211161c7bcf622c6)

This is a case study of what is built, what was measured, and what was not. Every
number below comes from a file in this repo, from a CI run you can open, or from a
command anyone can run without a key. Anything that only a paid run can show is marked
**not yet measured**. No quality score for the pipeline or for any model exists yet,
so none appears here.

## Contents

1. [The problem](#1-the-problem)
2. [The design](#2-the-design)
3. [What was measured](#3-what-was-measured)
4. [What failed, and what changed](#4-what-failed-and-what-changed)
5. [What is not built yet](#5-what-is-not-built-yet)
6. [What's next](#6-whats-next)

## 1. The problem

Most agent demos have two weak spots. The model grades its own work, and nothing
stands between what it produces and the outside world. A pipeline that writes public
posts needs three things a demo usually skips:

- **Checks the model can't talk its way past.** Word counts, citations and pass/fail
  decisions computed in code, not reported by the model.
- **A human decision before anything leaves the system.** Asynchronous, so a run can
  wait hours for an answer and survive a restart while it waits.
- **An honest record of what is measured.** A suite that always passes and a score
  nobody checked against a person both look like evidence and aren't.

Proofline is built around those three. Its first job is small on purpose: turn a topic
and an audience into a short technical script (140 words at most by default, about
56 seconds spoken at 150 words a minute) that works as a LinkedIn post and a 1-minute
video, with every claim cited. Proofline v1 is Measured Ops
([ADR-0013](adr/0013-measured-ops-is-proofline-v1.md)): a LangGraph supervisor with
specialist agents that runs a real work plan, with a human approving anything public,
any deploy, any spend and any message to a real person. The content pipeline is its
first agent.

## 2. The design

Three packages in one uv workspace, with dependencies pointing one way only:
`proofline` → `measured_ops` → `measured_evals`
([ADR-0012](adr/0012-workspace-split-and-week1-reset.md),
[ADR-0014](adr/0014-measured-ops-package-and-approval-ownership.md)). Both arrows
are enforced: `measured_ops` never imports `proofline`, `measured_evals` imports
neither, boundary tests fail if that changes, and CI installs each lower package on
its own and runs its tests.

### 2.1 The supervisor

```
START → call_agent → evaluate → triage ─┬→ gate ─┬→ gate     (more actions waiting)
                                        │        ├→ revise → call_agent
                                        │        └→ END
                                        └→ END   (nothing waits for a human)
```

- **Agents are injected, never imported.** Every agent is a compiled LangGraph
  subgraph with input `{"task": Task}` and output `{"result": AgentResult}`. Its own
  state stays private. An agent only *proposes* actions (type, target, preview, risk
  level). It never executes anything that leaves the system.
- **Agents never score themselves.** `run_agent` rejects an action that arrives with
  eval scores. Only the `evaluate` node writes them
  ([ADR-0008](adr/0008-critic-rubric-scoring.md), [ADR-0018](adr/0018-eval-guard-node.md)).
- **A policy table decides, in one place** (`policy.decide`, demo table in
  `measured_ops/config/approval_policy.demo.toml`): `auto`, `approval` or `never`.
  An action type the table doesn't list waits for a human. `risk_level = "high"`
  waits even on an `auto` row. A `never` row is blocked and logged, never reaches the
  inbox, and no approval unblocks it. A test checks every combination of row, risk
  level and eval result.

### 2.2 The approval gate

The gate pauses with `interrupt()` and resumes from a checkpoint (SQLite locally,
Postgres in CI). Two rules came out of building it
([ADR-0016](adr/0016-supervisor-graph-and-approval-gate.md)):

- **Nothing with side effects before `interrupt()`.** The node re-runs from its first
  line on every resume, so classification happens in `triage`, before the gate.
- **The gate never raises after `interrupt()` returns.** The answer is validated
  inside `interrupt(..., response_schema=...)`, before LangGraph saves it. A wrong
  action id, a malformed answer or an approval for a blocked action raises to the
  caller and leaves the run paused for a correct answer. Section 4 explains how the
  obvious version of this code failed.

The end-to-end test pauses a run in a **separate process**, then resumes and finishes
it in the test process, so the resume can only come from the checkpoint. It covers
approve, reject, a refused answer, a failed check and a clean auto action, on SQLite
and on Postgres. In CI the Postgres case fails instead of skipping when its database
URL is missing, so it can't be skipped quietly.

### 2.3 The eval/guard node

`evaluate` runs `measured_evals.evaluate()` on every proposed action's preview, and
the injection and PII checks on its target, then writes scored copies to their own
state field. The agent's result stays exactly as the agent returned it.

guard-v0 is deterministic: no model, no network, no key. Each check scores 0 or 1:

| Check | Looks for |
|---|---|
| `quality` | empty text, over the word limit, leftover placeholders, missing citations when they are required |
| `injection` | override phrases, role and system tags (including a stray `</source>`), invisible or bidi characters, links with query parameters |
| `pii` | emails, phone numbers, SINs and card numbers (Luhn-checked); fictional values count too |

The escalation rule is **fail closed, never lower**: a missing or failed score raises
an `auto` action to `approval` and changes nothing else. Check reasons name the kind
of problem ("email found"), never the matched text, so a personal value can't leak
into scores, logs, traces or the inbox's check rows. LangSmith traces go through a
tracer whose client redacts the same patterns before anything is sent.

These checks are heuristics. ADR-0018 lists their known false positives (every
sourced link with a query string, some emoji) and false negatives (rephrased
overrides, data hidden in a link path).

### 2.4 The content agent

`ContentAgent` wraps the original pipeline as one node of an agent subgraph
([ADR-0019](adr/0019-content-agent-and-revision-loop.md)):

```
planner → researcher → writer → critic ─┬→ writer      (score below 8.0, revisions left)
                                        └→ finalizer   (score passes, or 3 revisions used)
```

- **Dependencies come from LangGraph's runtime context** (an LLM port and a
  `Researcher` port), never from globals
  ([ADR-0003](adr/0003-runtime-context-ports.md)). The whole graph runs in tests with
  a fake model.
- **Derived numbers are computed in code**
  ([ADR-0002](adr/0002-typeddict-state-pydantic-values.md),
  [ADR-0011](adr/0011-week1-contract-lock.md)): word count, cited source ids and the
  critic's overall score. The critic returns eight per-criterion scores, each with a
  quoted piece of evidence and a fix. Python computes the weighted mean and caps it at
  6 when technical accuracy is below 6.
- **Source ids come from code.** Citations to ids that don't exist are dropped and
  logged. Web content reaches the model escaped, inside tags, as data and never as
  instructions ([ADR-0007](adr/0007-research-sources.md)).
- **Two exits for the critic loop**, both decided by a pure function: the score
  reaches the threshold (8.0 by default), or the revision budget (3) is used up. A
  recursion limit (40) is the backstop.
- **One `approve_draft` action per topic.** Its preview holds three sections
  rendered in Python with no extra model call: the LinkedIn post with its sources,
  the clip script without citation marks, and a lesson outline from the planner.
  **`approve_draft` has no executor, now or later.** Approving it only moves it to
  Ready to paste. A test pins that.
- **A rejection with feedback revises in the same thread**, at most 3 times. The
  revision number travels in the task, and the rejected preview reaches the writer as
  context.

### 2.5 The inbox

A small FastAPI app, server-rendered, reached from a phone
([ADR-0017](adr/0017-approval-inbox.md), [ADR-0020](adr/0020-ready-to-paste-and-explicit-reject.md),
[`INBOX.md`](INBOX.md)):

- It binds to `127.0.0.1` only and is reached over `tailscale serve`, tailnet only.
  `tailscale funnel` is never used.
- One passphrase, stored as a scrypt hash (n = 2^17). The app refuses to start
  without its secrets. Five failed sign-ins lock sign-in for 15 minutes, and attempts
  run one at a time. Sessions end 12 hours after sign-in, and signing out revokes
  them on the server. Every form post carries a CSRF token. The CSP allows no inline
  code and no outside origin; the only script is the inbox's own copy helper.
- Each waiting action shows its preview, its checks with reasons, where the run is
  ("First draft · up to 3 revisions", "Revision 2 of 3") and the run's tokens and cost
  per agent.
- Three explicit buttons: **Approve**, **Revise with this feedback** (needs text and a
  revision left) and **Reject and stop** (ignores any text). A second tap on an answer
  that is already recorded gets "Already decided".
- **Ready to paste** lists approved drafts with a Copy button per block. **Mark as
  posted** only records an action id and a time. Nothing is ever published from the
  inbox.
- A revision runs in the background, one at a time, under a 600-second timeout, with
  a Retry button and a sweep on restart.

### 2.6 How cost is counted

Cost per agent run is designed in, even though no real run's cost is recorded in this
repo yet (section 3):

- A LangChain callback collects every chat-model call inside an agent's subgraph and
  records one `Usage` per call: input, output, cache reads, and cache writes at
  5 minutes and 1 hour, all counted separately because they are priced differently.
- `cost(usage, prices)` prices it from a table. **A model missing from the table
  costs `None` ("unknown"), never 0.** The inbox shows "unknown", never "free".
- Prompt-length tiers are priced **per call**. A multi-call total on a tiered model
  costs `None`, because it can't tell which calls crossed the threshold, and a guess
  could under-price.
- The public table (`measured_ops/config/model_prices.demo.toml`) is checked against
  the provider's pricing page (checked 2026-11-14). Default models: Haiku 5.5 for the
  planner and researcher, Sonnet 5.5 for the writer and critic (`.env.example`).
- Known gaps (ADR-0018): calls made outside LangChain aren't counted, and batch,
  regional and other price modifiers aren't modelled.

## 3. What was measured

### 3.1 Tests

| What | Result | Where it comes from |
|---|---|---|
| Fast suite, local, no keys, no Postgres | **862 passed, 9 skipped, 5 deselected** | `uv run pytest -m "not live"` at `8fdc21c`, run on 2026-11-23 for this case study. The 9 skips: 8 Postgres cases (no `MEASURED_OPS_TEST_DATABASE_URL`) and 1 DeepEval test (DeepEval not installed). The 5 deselected are live tests |
| Fast suite, CI, with a Postgres service | **870 passed, 1 skipped, 5 deselected** | [CI run 44321784485](https://github.com/hassanmansour-dev/ProofLine/actions/runs/44321784485) on `8fdc21c` |
| `measured_evals` installed alone, CI | **307 passed, 1 skipped** | same run |
| `measured_ops` installed alone, CI | **333 passed** | same run |
| Lint, format, types | ruff check and ruff format clean; mypy strict: no issues in 58 source files | run locally at `8fdc21c` |

How the suite grew, per step, from the PR checks recorded in [`PLAN.md`](PLAN.md) (all
with the Postgres e2e case running, 0 skipped):

| Step | PR | Tests passed in CI |
|---|---|---|
| C2 supervisor and gate | #9 | 273 |
| C4 approval inbox | #10 | 331 |
| C7 eval/guard node (after review fixes) | #12 | 483 |
| C8 content agent, with Ready to paste | #13 | 600 |
| Evals and licence work, `main` at `8fdc21c` | later PRs, through #23 | 870 (plus 1 skipped) |

Test counts show coverage of behaviour. They say nothing about the quality of what
the pipeline writes.

### 3.2 The deterministic eval tier

The golden set ([`GOLDEN_SET.md`](GOLDEN_SET.md)) has **20 topics**: 15 standard,
3 with injection bait, 2 unanswerable, with **35 judge assertions** that the
deterministic tier leaves for the model-graded tier. The owner approved all 20 on
2026-11-17. Approving a question answers nothing.

| Run | Result | What it means |
|---|---|---|
| `python -m measured_evals run` (fake grounded writer) | 20/20 items passed, 35 judge assertions not run | **Wiring, not quality.** The fake writer builds text from the fixture sources and passes by construction. It shows the set, the suite and the format agree |
| `python -m measured_evals run --writer fake-careless` | 0/20 items passed | The checks can fail. CI fails if this writer ever passes |
| `--baseline evals/baselines/content_v0.placeholder.json` | 0 regressions · `NOT ENFORCED` | The only baseline is a placeholder from the fake writer. It gates nothing |

All three runs reproduce with no key (run on 2026-11-23 at `8fdc21c`).

### 3.3 The inbound injection corpus

ADR-0021 (status: proposed) adds a screen for content coming *in* and a demo corpus
that keeps its misses. On that corpus: **12 of 16 attacks flagged, 2 of 7 benign texts
flagged.** These counts describe a small, hand-written demo corpus. They are not
detection rates for real content. Neither the screen nor the budget guard is wired into
the running system yet.

### 3.4 Live runs

What the repo records:

- **First live run with a real key** (before C1). It recorded a 400 from the API and
  a draft that was too long. Section 4 covers both.
- **First real draft in the inbox** (C8, 2026-11-16): `proofline propose` with real
  keys, then the inbox on the phone. The live test passed.
- **Phone check of the inbox** over Tailscale (C4, 2026-11-14).

### 3.5 Not yet measured

GROW-16 asks for an eval table, judge agreement and cost per run. None of these
exist yet, and none is estimated here:

| Measure | Status | What produces it |
|---|---|---|
| Deterministic pass rate of **real** pipeline output on the golden set | **not yet measured** | a real run over the 20 items, then `measured_evals run --outputs DIR` ([`EVALS.md`](EVALS.md)) |
| Judge scores (35 assertions) | **not yet measured** | the model-graded tier, never run against a model ([`MODEL_GRADED.md`](MODEL_GRADED.md)) |
| Critic agreement with a person (Cohen's kappa) | **not yet measured** | a blind calibration sheet ([`CALIBRATION.md`](CALIBRATION.md)) |
| Cost per script, tokens per run | **not yet measured** (not recorded in the repo) | the inbox's cost panel on real runs, recorded per run |
| Revisions per draft, score trajectory, latency | **not yet measured** | the judge log on real runs (C9) |
| A real regression baseline | **not yet measured** | step 3 of "Making it real" in [`EVALS.md`](EVALS.md) |

## 4. What failed, and what changed

Every item here is recorded in an ADR, [`PLAN.md`](PLAN.md) or the
[prompt changelog](../packages/proofline/src/proofline/prompts/CHANGELOG.md).

**The first live run failed in the planner.** The API returned a 400: `temperature`
is deprecated for the current models, and the run config set one for every role.
ADR-0008 had relied on critic temperature 0 for steadier scores. Fix
([ADR-0015](adr/0015-no-temperature-by-default.md)): send no temperature by default.
Critic consistency now has to come from the anchored rubric and the score computed in
code, measured against a person, not from a sampling setting.

**The first live draft was 195 words**, too long for a 1-minute video. The default
limit became 140 words, about 56 seconds at 150 words a minute (C8). The same run
showed two more problems. The writer described the critic loop as live when it wasn't
wired yet, so the loop was wired in C8. And the hook was weak because
`docs/BRAND_VOICE.md` was empty. The owner has since written that file. No live run
after that change is recorded in this repo.

**Five contract bugs found before the first live run**
([ADR-0011](adr/0011-week1-contract-lock.md)), each checked against the installed
packages:

1. Structured output on the writer and critic model used unforced tool calling, so a
   prose reply skipped the repair retry. Fix: `method="json_schema"` with the raw
   output kept.
2. A critique that repeated a criterion passed validation, then crashed the router.
   Fix: a model validator, so the adapter's repair retry handles it.
3. Cited source ids were reported by the model and could disagree with the text. Fix:
   ids are parsed from the text, normalized, and unknown ones dropped.
4. The target length was reported by the model. Fix: set in code.
5. The researcher's retry policy retried the wrong failures (an invalid key yes, a
   timeout no). Fix: an explicit `SearchUnavailableError`.

**The obvious approval gate didn't work** ([ADR-0016](adr/0016-supervisor-graph-and-approval-gate.md)).
The first version checked the human's answer after `interrupt()` returned and raised
on a bad one. On LangGraph 1.2.16 the value `interrupt()` returns is saved as the
task's resume write. Raising after that left the bad answer saved, and it replayed on
every later resume: the run could never be answered. Fix: validate inside
`interrupt(..., response_schema=...)`, which fails before the value is saved. A test
pins the case.

**Parallel sign-in guesses were all checked before any failure counted** (C4 review,
ADR-0017). Fix: sign-in attempts run one at a time.

**C7 review fixes** (PR #12, ADR-0018): a cache-read price corrected to the published
rate for one model, a prompt-length price tier priced per call instead of per total,
PII masked in LangSmith traces, and the blocked-action log stripped of the target (a
target can be an address).

**C8 review** (ADR-0019): agent runs never checkpoint. Agents' private state types
aren't in the supervisor's serde allowlist, so a run cut off mid-agent restarts the
agent from the start.

**Two usability gaps found in review** ([ADR-0020](adr/0020-ready-to-paste-and-explicit-reject.md)).
An approved draft left the inbox and was saved nowhere it could be copied from. And one
Reject button that revised when the box had text, and stopped when it was empty, was
too easy to get wrong on a phone. Fix: the Ready to paste page, and three explicit
buttons. That needed the inbox's first script (copy to clipboard), pinned by the CSP to
the inbox's own origin. Every page still works without it.

**Scope changes, each with an ADR.** The project started as a 13-week plan for a
single pipeline producing 60-second and 10-minute scripts. It was reset to one short
format and a standalone eval package
([ADR-0012](adr/0012-workspace-split-and-week1-reset.md)), then became Measured Ops
with a plan in ordered steps and no dates
([ADR-0013](adr/0013-measured-ops-is-proofline-v1.md)). Human review moved out of
the pipeline into the supervisor's gate: ADR-0005 was superseded by ADR-0014, because
two approval paths are one too many.

**Found while building the bootstrap** (C0, [`PLAN.md`](PLAN.md)): a new Vercel
project's first deploy goes to production even with `--environment preview`. The
bootstrap now treats that deploy as production, asks first, and checks every
deploy's target.

## 5. What is not built yet

From [`PLAN.md`](PLAN.md), in order:

- **C5, MCP tools** (Jira and a personal calendar), in progress. Reads run freely, and
  every write goes through the approval policy. **Until C5, nothing in `ready`
  executes**: an approved action is a recorded decision.
- **C6, the orchestrator** (a weekly plan by order and available time). Until then,
  topics go in by hand with `proofline propose`.
- **C9, a week of real use**, and editing a draft in the inbox (it needs an ADR, and
  the edited text has to be re-checked before it can be approved).
- **C10, scheduled runs** with budget caps, a kill switch and hosted Postgres. Until
  then, revisions run inside the inbox process.
- **C11, more agents**, one at a time.
- **Evals and hardening:** a real baseline, a judge run, critic calibration, wiring the
  inbound screen and the budget guard, auth beyond one passphrase, and publishing
  `measured-evals` to PyPI.

And the measurement gap, stated plainly: **the golden set has not been run against real
pipeline output.** One piece is missing before it can be. Nothing in the repo yet feeds
a golden item's fixture sources to the pipeline and saves the output as
`<item-id>.md`. `proofline run` researches with its own stub notes or web search,
so its citations wouldn't refer to the item's sources.

## 6. What's next

1. **A golden-set runner**: a researcher that serves a golden item's fixture sources,
   and a command that writes one `<item-id>.md` per approved item. It's a code change
   with its own tests, after C5.
2. **The first real run over the 20 items**, then the deterministic tier on those
   outputs and a draft baseline the owner reviews ([`EVALS.md`](EVALS.md)).
3. **Critic calibration** on the same outputs, scored blind
   ([`CALIBRATION.md`](CALIBRATION.md)).
4. **The model-graded tier**: first a single item with a low call cap, then the full
   set ([`MODEL_GRADED.md`](MODEL_GRADED.md)).
5. **C9**: a week of daily use. The rejections and edits reshape the weakest golden
   items.
6. This case study gets a measured-results section only when steps 2 to 4 produce
   numbers with a source.

---

Reproduce every keyless number here:

```bash
uv sync --locked
uv run pytest -m "not live"                                          # tests
uv run python -m measured_evals run                                  # 20/20, fake writer
uv run python -m measured_evals run --writer fake-careless           # 0/20
uv run python -m measured_evals run --baseline evals/baselines/content_v0.placeholder.json
```

Built by Hassan Mansour on his own time, as a public portfolio project. Everything in
this repo is demo config and sample data.
