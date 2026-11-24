# ADR-0023: Staging pieces: kill switch, per-run budget, digests, Postgres logs

- Status: proposed (for Hassan's review; C10 parts that need neither C9 nor his accounts)
- Date: 2026-11-23
- Phase/Step: Phase 1 / C10 (GROW-89)

## Context
C10 (GROW-89) runs the slice unattended as scheduled jobs: daily digests at 7 AM and
9 PM, budget caps enforced, a kill switch, own infra only, staging then production;
done when it runs unattended for 3 days with digests arriving. docs/PLAN.md adds two
items: the bootstrap's first hosted Supabase run (Hassan, 2026-11-14: in the SanoVinci
organization) and moving the judge log and posted marks into Postgres when runs are hosted.

Some of this needs Hassan: his accounts (hosted Supabase, wherever the worker runs), a
deploy (an approval-policy action), the channel a digest reaches his phone by (any of
them messages a real person through an outside service), the real cap amounts, and three
days of watching. The rest is code that can be built and tested now. ADR-0021 built the
budget guard in `measured_evals` and left wiring it to "after C5". Nothing here depends
on C9 (a week of real use).

Verified for this change on langchain-core 1.6.12: a callback handler with
`raise_error = True` that raises in `on_chat_model_start` aborts the call, and the
exception reaches the caller unchanged.

## Decision
1. **Kill switch** (`measured_ops.killswitch`): a file holding when and why:
   `MEASURED_OPS_KILL_SWITCH_PATH` (absolute; a relative path is refused), default
   `~/.measured_ops/kill_switch`, so every process on the machine watches the same file
   whatever its working directory. While it exists, the supervisor's `call_agent` raises
   `KillSwitchEngaged` before any agent runs (the run stops between nodes, resumable
   after release), **`execute` raises it before any executable action** (no approved or
   "auto" write happens; the decision stays saved and the action runs once after
   release; a "manual" outcome runs nothing and isn't stopped), the inbox refuses Retry,
   skips its start-up sweep and says so on the page, and `proofline golden` starts no item.
   **Every supervisor reads it by default** (`KillSwitch.from_env()`), so a runner can't
   forget it. Engage or release with `python -m measured_ops.worker kill|release`, or by
   hand: no process has to be reachable.
2. **Per-run budget** (`measured_ops.budget`, on `measured_evals.budget`):
   `build_supervisor(...)` takes its budget **by default from the table
   `MEASURED_OPS_BUDGET_PATH` names** (like the kill switch: no runner has to wire it;
   `budget=None` opts out; unset means no caps) and checks the run's recorded usage
   before each agent run, and a `raise_error` callback (set through a context variable,
   like `collect_usage`) checks before **every model call** inside the agent. The calls
   cap counts the call about to be made; tokens and cost are checked on spend so far
   (the supervisor doesn't know a call's `max_tokens`), so a run can exceed a token or
   cost cap by at most the call that crossed it. Unknown cost fails closed (ADR-0021).
   **Crossing a cap ends the run**: `call_agent` returns a failed `AgentResult`
   (`error = "budget: ..."`) with that pass's usage recorded. It doesn't raise, because a
   raise loses the state update and every Retry would start from a fresh allowance. A
   model call that reports no usage counts as one call on the "unknown" model, so a
   calls cap sees it and a cost cap fails closed. `proofline golden` applies the same
   budget per item. Caps come from TOML (a placeholder demo table ships).
3. **Digest** (`measured_ops.digest`): read-only Markdown from the checkpointer through the
   inbox service: waiting actions (oldest first, how long), stopped and in-progress runs,
   approved drafts not posted, the cost of runs active in the window (an unpriced model is
   "unknown", never 0), and the kill switch. Targets never appear. Delivered by a
   `DigestSink`; the only one here writes `outputs/digests/<UTC minute>.md`.
4. **Worker** (`python -m measured_ops.worker`): `digest` (once), `run` (a digest at each
   `--at` time, default 07:00 and 21:00 `America/Toronto`, resolved per day so it follows
   daylight saving; one process, lock file), `kill`, `release`, `status`, `import-logs`.
   It runs no agent.
5. **Postgres logs** (`measured_ops.pg_logs`): the judge log and posted marks behind their
   existing ports, selected by `MEASURED_OPS_LOGS_URL`; tables created if missing;
   `import-logs` copies the JSONL files in idempotently (a line's hash, an action id).
   The ports stay synchronous, so each write is one short round trip.

## Alternatives considered
- **Kill switch as an env var** — needs every process restarted to take effect; a file
  takes effect on the next agent run in every process.
- **Kill switch in the database** — survives a moved laptop, but needs the database up to
  stop things; a file works when Postgres is the problem. Revisit when hosted.
- **Budget only between agent runs** — one runaway agent run (a critic loop) could spend
  the whole day; the per-call check stops it inside the run.
- **Estimate the planned call's cost** — needs each role's `max_tokens` in the supervisor,
  which knows nothing of agent internals; the overshoot is bounded by one call instead.
- **Send digests by email now** — a channel, an account and a recipient are Hassan's
  decisions, and sending is "message a real person" in the policy.
- **Async log ports** — every caller (the content agent, the inbox) would change for a
  few writes per decision.

## Consequences
- Good: every run can be stopped from a terminal in one command; a runaway loop stops at
  the cap before its next call; a digest shows what needs attention without opening the
  inbox; the logs can move to Postgres with one env var and one import.
- A run that ended as failed is shown (second review, 2026-11-24): ending the run on a
  budget stop made it neither waiting nor stopped, so nothing told the human that no
  draft was coming. The inbox lists runs that ended with a failed result in the last 7
  days (read from the checkpointer: a failed first pass from another process and a
  restart are covered), the digest has a Failed section, and a revision that ends
  failed is logged at ERROR.
- Log setup is explicit and loud: the Postgres adapters' `setup()` runs once when the
  inbox starts and before the content agent's first run, and may raise; later write
  errors are logged and never undo a decision. Construction still does no I/O.
- `KillSwitch.from_env()` warns when a file exists at the first default
  (`./.data/kill_switch`) and not at the current one. `proofline golden`'s budget caps
  each item, not the whole run.
- Known limit: a pass cut off by something other than its budget (a timeout, a crash,
  the kill switch) still loses that pass's usage with its state update; the earlier
  passes' usage is kept, and such a run continues only by a manual Retry or once per
  inbox start. `proofline run` (the bare pipeline CLI) has neither stop.
- Tests never see a developer's exported `MEASURED_OPS_LOGS_URL`, `..._BUDGET_PATH` or
  `..._TOOLS_CONFIG`, and can open Postgres logs only in the test database (root conftest).
- Postgres log adapters connect on first use, never at construction, and every caller on
  an event loop runs them in a worker thread.
- Bad / cost: a daily (not per-run) cap isn't enforced yet: nothing starts runs on a
  schedule until C6, so per-run caps and the kill switch cover today's runners. The digest
  costs whole runs active in the window, not spend inside it. The kill switch file is per
  machine.
- Needs Hassan: real caps (private `MEASURED_OPS_BUDGET_PATH`); the digest's delivery
  channel; where the worker runs (his machine with systemd, or hosted) and the hosted
  Supabase project in the SanoVinci org (bootstrap's first real run); the deploy; three
  unattended days.
- Revisit when: C6 starts runs on a schedule (daily cap, revisions moved from the inbox to
  this worker, ADR-0019); runs are hosted (kill switch and logs in the database); a digest
  channel is chosen.
