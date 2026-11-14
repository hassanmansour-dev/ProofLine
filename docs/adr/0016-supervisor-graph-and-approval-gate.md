# ADR-0016: Supervisor graph, approval gate and Postgres checkpointer

- Status: accepted (2026-11-14, Hassan's review on PR #9). Amends
  ADR-0014 on `pending_actions` ownership (see State).
- Date: 2026-11-14
- Phase/Step: Phase 1 / C2 (GROW-81)

## Context
C1 (ADR-0014) fixed the contract and left C2 to wire it: a supervisor graph with a
stub agent, a Postgres checkpointer, an interrupt before any action that needs
approval, and an e2e test in CI. A few questions were still open:

1. Where each proposed action ends up. ADR-0014 gave `OpsState` `blocked`
   (never) and `decisions` (human answers), but nowhere to put an action the policy
   runs automatically or a human approves, and no tools exist yet to run it (C5).
2. Who owns `pending_actions`, which has no reducer.
3. Where `policy.decide` runs. It logs blocked actions at WARNING, and the gate
   node re-runs from its first line on every resume.
4. How the gate refuses a bad answer (wrong action id, malformed payload, an
   approval for a "never" action) without breaking the paused run. Found while
   building: on langgraph 1.2.16, the value `interrupt()` returns is saved as the
   task's own resume write. If the node raises *after* `interrupt()` returns,
   that write stays, and every later `Command(resume=...)` gets the bad value
   again: the run can never be answered.
5. Which Postgres. The plan says Supabase, but the hosted Supabase project moved
   to C10 (free tier full).

## Decision

**Graph.** `build_supervisor(agents, policy) -> StateGraph[OpsState]`, compiled by
the caller with a checkpointer:

```
START → call_agent → triage ─┬→ gate ─┐   route_pending: "gate" while anything
                             │   ▲    │   is pending, else END (pure, path-mapped)
                             │   └────┤
                             └────────┴→ END
```

- `build_supervisor` builds and runs `check_agent_graph` on every agent at startup,
  and rejects duplicate names. Agents are still injected, never imported (ADR-0014).
- `call_agent` runs `state["task"]` through its agent with `run_agent` and appends
  the `AgentResult`. A task for an unknown agent raises `AgentContractError`.
- `triage` classifies every action of the latest result **once**, with
  `policy.decide`: "auto" → `ready`, "never" → `blocked` (logged by the policy),
  "approval" → `pending_actions`. A failed result proposes nothing and the run ends.
- `gate` handles the head of `pending_actions`: `interrupt(payload)`, then the
  decision goes into `decisions`, the head is popped, and an approved action is
  appended to `ready`. One action per pause, in the order the agent proposed them.

**State.** New field `OpsState.ready: Annotated[list[PendingAction], operator.add]`.
Every proposed action ends in exactly one place: `ready`, `blocked`, or rejected
(its `ApprovalDecision` in `decisions` is the record). Nothing executes `ready`
until the tools arrive in C5. `pending_actions` stays reducer-free with two
sequential writers (`triage` sets it, `gate` pops it). This **amends ADR-0014**,
which asked C2 for a single owner or a reducer: two writers that never run in the
same superstep are safe while one agent runs at a time, and it must change before
agents run in parallel.

**Gate payload.** The interrupt value is JSON-safe: `action_id`, `task_id`, `type`,
`target`, `preview`, `risk_level`. The resume value is
`ApprovalDecision.model_dump()`.

**Bad answers are refused inside `interrupt()`.** The gate passes
`interrupt(..., response_schema=answer_schema(action, policy))`, an
`Annotated[ApprovalDecision, AfterValidator(...)]` that requires the decision's
`action_id` to match the waiting action and calls `policy.check_decision` (no
approval unblocks a "never" action; this fires when the policy got stricter
between pause and resume). LangGraph validates before it binds the resume value
to the gate task, so a bad answer raises to the caller (`ValidationError`, or
`ActionBlockedError`) and the run stays paused at `gate` for a correct answer.
Precisely: nothing is bound to the gate task, but the refused value stays as the
thread's pending (null-task) resume write until the next `Command(resume=...)`
replaces it. So `ainvoke(None, cfg)` after a refusal replays the refused answer and
raises again instead of re-pausing; callers answer with a new `Command`, never a
bare re-run. The value in state is still a plain `ApprovalDecision` (no new
serde allowlist entry). The interrupt also carries the answer's JSON schema, which
the inbox (C4) can use. Rule: **the gate never raises after `interrupt()` returns.**

**Rejection.** A rejection is recorded and the run ends. Turning feedback into a
revision `Task(parent_id=...)` lands with the content agent in C8, together with
the draft-in-inputs gap ADR-0014 records.

**Checkpointer.** `measured_ops.checkpoint.open_checkpointer(url)`: `memory://`,
`sqlite:///path`, `postgresql://...`, always with `contracts.SERDE_ALLOWLIST`.
Postgres runs `setup()` on open (idempotent). Its drivers
(`langgraph-checkpoint-postgres`, `psycopg[binary,pool]`) are an optional
`postgres` extra on `measured-ops` and a dev dependency of the workspace, so
`measured_ops` still imports and runs on memory or SQLite without them. Supabase
is plain Postgres to this code: the same URL scheme works locally (Docker), in CI
(service container) and on hosted Supabase (C10).

**e2e test.** `tests/test_ops_e2e.py`: task → echo agent → pause, in a **separate
process** (`ops_e2e_child.py`), then resume and finish in the test process, so the
resume can only come from the checkpoint. Approve and reject cases, on SQLite and
Postgres, with `LANGGRAPH_STRICT_MSGPACK=true`. The Postgres case reads
`MEASURED_OPS_TEST_DATABASE_URL`. Locally it skips without the URL. In CI it
**fails** without it, so it can't be skipped quietly. CI's `checks` job runs a
`postgres:17-alpine` service with throwaway credentials.

## Alternatives considered
- **Check the decision after `interrupt()` returns and raise.** The obvious code,
  and what C2 first did. It doesn't work: the bad resume value stays saved and
  replays on every later resume (pitfall above; covered by
  `test_answer_for_another_action_is_refused_and_the_run_stays_paused`).
- **On a bad answer, call `interrupt()` again in the same node.** The run stays
  answerable, but the caller gets a fresh pause instead of an error, and the
  node's resume list grows with each bad answer. An error the inbox can show is
  clearer.
- **Classify inside the gate.** `decide` logs, and the gate re-runs on resume, so
  every blocked action would be logged once per pause (ADR-0014 already ruled this out).
- **One pause for all pending actions (a list of decisions).** Fewer round trips,
  but partial answers, ordering and validation get harder. One action per pause
  matches how the inbox (C4) shows items on a phone. Revisit if pauses get noisy.
- **Add the agent subgraph as a node directly.** Agents would share `OpsState`
  keys. ADR-0014 rejected it; `run_agent` inside `call_agent` keeps agent state private.
- **Postgres drivers as hard dependencies of `measured-ops`.** Simpler install,
  but every user of the contracts would pull in psycopg. The extra keeps the base
  package light.

## Consequences
- Good: one gate for every agent, refusals that don't break a paused run, a
  restart-proof e2e test on the same database engine production uses, and no
  change to the serde allowlist.
- Bad / cost: the gate's correctness depends on LangGraph validating
  `response_schema` before saving the resume write (verified on 1.2.16, and the
  supervisor tests and the e2e refusal case on SQLite and Postgres pin it).
  Refusals arrive as two exception types: `ValidationError` (marked by
  `langgraph.errors.is_invalid_resume`) and `ActionBlockedError` (a
  `PermissionError`, **not** marked), so callers (C4) must catch both to tell a
  refused answer from a crashed graph. `ready` grows without anything consuming it until
  C5. CI needs a Postgres service.
- Revisit when: agents run in parallel (`pending_actions` needs a reducer or one
  owner); C5 executes `ready`; C7 adds eval scores to the payload or forces
  approval below a threshold; C8 turns rejection feedback into revision tasks;
  LangGraph changes how resume writes are saved; C4 builds retry or crash
  recovery on top of the gate (never resume with a bare `ainvoke(None)`); a network
  agent arrives (C8: `call_agent` needs a `RetryPolicy`).
