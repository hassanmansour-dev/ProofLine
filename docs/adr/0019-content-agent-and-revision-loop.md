# ADR-0019: Content agent v0, approve_draft, and revisions in the same thread

- Status: proposed (2026-11-14, C8; written before the supervisor changes, for Hassan's review)
- Date: 2026-11-14
- Phase/Step: Phase 1 / C8 (GROW-87)

## Context
C8 turns the content pipeline into the first real agent. Hassan's decisions
(2026-11-14): start before C5/C6, with topics entered by hand (`proofline propose`);
**one action per topic**, its preview holding the LinkedIn post, the 1-minute clip
script and the lesson outline (PLAN's C8 box lists all three); rejections with feedback
**revise in the same thread, at most 3 times**; inbox edit moves to C9; no
supervisor-level retry; a judge log with action id, critic scores, eval scores and the
decision only. Earlier ADRs left C8 three things: the `measured_ops` dependency
(ADR-0014), revision Tasks from rejection feedback (ADR-0014, ADR-0016), and the
previous draft reaching the writer on a revision (ADR-0014's gap).

## Decision

**1. Content agent** (`proofline/agent.py`). `ContentAgent(deps, judge_log)`, name
`content`, implements `measured_ops.Agent`: a subgraph with input `{"task"}` and output
`{"result"}`. Its one node maps the Task to the pipeline's input state (`inputs.topic`,
`inputs.audience`, `feedback`, `inputs.previous_preview`), runs the pipeline graph
(planner → researcher → writer ⇄ critic → finalizer) with `context=deps`, and returns
an `AgentResult` with one action. Dependencies come through the agent's constructor, so
the supervisor doesn't change for it. `proofline` takes the `measured-ops` dependency;
`measured_ops` still never imports `proofline` (boundary test).

**2. The critic loop is wired** (`writer → critic → route_after_critic`), as built and
tested since Week 2. Its score is the agent's own quality loop, never `eval_scores`
(ADR-0008: only the eval node writes those).

**3. One `approve_draft` action per topic.** `type="approve_draft"`,
`target="manual paste: linkedin"`, `risk_level="medium"`, and a preview of three
Markdown sections, all rendered in Python from what the pipeline already produced (no
new LLM calls, so this isn't the cut repurposing team):
- **LinkedIn post**: the final script (title, hook, body, call to action, sources);
- **Clip script**: the spoken text (hook, body, call to action) without citation marks,
  with its estimated length;
- **Lesson outline**: the planner's angle, sections (title and goal) and key questions.

The demo policy gets `approve_draft = "approval"`.

**4. `approve_draft` never publishes.** Approving it moves it to `ready` and nothing
else: no executor is ever registered for this type, now or in C5. An approved draft is
text Hassan pastes himself. A test pins that an approved `approve_draft` ends in
`ready` with no other effect, and C5's executor table must leave it out.

**5. Revisions in the same thread.** The supervisor graph becomes:

```
START → call_agent → evaluate → triage ─┬→ gate ─┬→ (more pending) gate
                                        │        ├→ revise → call_agent   (rejected with feedback, cap not reached)
                                        │        └→ END
                                        └→ END
```

- `gate` is unchanged: it records the decision and pops the action. It stays idempotent
  on resume: nothing before `interrupt()` has side effects, and nothing after it raises.
- A new pure router after `gate`: more pending actions → `gate`; otherwise, if this
  round has a rejection **with feedback** and fewer than `max_revisions` (default 3)
  revisions have run (`len(results) - 1`), → `revise`; otherwise → `END`. A rejection
  without feedback ends the run, as today.
- `revise` builds the next `Task`: same agent and goal, `parent_id` = the task it
  revises, `feedback` = the rejection feedback (several joined, if several actions were
  rejected), and `inputs` = the old inputs plus `previous_preview` (the rejected
  action's preview text). It writes `task`, and the graph goes to `call_agent`. No
  interrupt, no I/O, so it runs once per pass.
- The previous draft reaches the writer without a contract change: the supervisor
  doesn't know agent internals, so it passes the preview text, and
  `render.revision_context` gains a feedback-only branch that uses
  `previous_preview` when there is no `draft` in state (ADR-0014's gap, with a test).
- No new state field: the revision count is `len(results) - 1`. Every pass appends to
  `results`, `decisions`, `usage`; `evaluated_actions` is replaced by the new pass.
  `recursion_limit` stays a backstop (4 passes × 5 nodes fits in 40).

**6. Where a revision runs: the inbox, in the background.** Rejecting with feedback
resumes the run, which now goes on into `call_agent`: a full content-agent run (research,
up to 4 drafts and critiques), minutes, not seconds. The inbox's `decide` therefore
resumes the thread in a background task and answers at once ("Revising…"); the run
appears in the list again when it pauses at the gate. This needs:
- the inbox process to be built with the agents (the private repo's `serve` passes the
  content agent; the public demo can't, since `measured_ops` never imports `proofline`,
  so `proofline` gets a small `proofline inbox` command that serves it with the content
  agent and demo config);
- a startup sweep: a thread whose latest checkpoint is between nodes (not at `gate`, not
  finished) is a revision cut off by a restart, and the inbox continues it with
  `ainvoke(None)`. That is safe there because no interrupt is pending (ADR-0016's rule
  forbids it only at the gate). A thread that fails is logged and left for a human.
- The page shows runs in progress as "Revising…", so a revision isn't invisible.

**7. Judge-agreement log** (`proofline/judge_log.py`, a port with a JSONL adapter).
Two kinds of line, and **only these fields**, never draft text, previews or targets:
- written by the content agent when it proposes: `{"kind": "critic", "action_id",
  "critic": {criterion: score, ..., "overall": float}}`;
- written by the inbox after a decision is accepted: `{"kind": "decision",
  "action_id", "eval": {metric: score, ...}, "approved": bool}`.
GROW-13 joins them on `action_id`. Default path `.data/judge_log.jsonl` (gitignored);
the private repo sets its own. A test pins the exact key sets and that no draft text or
target appears.

**8. Tracing.** `proofline run` and `proofline propose` pass
`masked_tracing_callbacks()` (ADR-0018), so the CLI's traces are PII-masked too. Inside
the supervisor, the content agent's pipeline call passes no `callbacks` key, so it
inherits the supervisor's masked tracer. Runs are named `agent:content`.

**9. No supervisor-level retry.** Retrying `call_agent` would re-run the whole agent
and pay for every LLM call again. The researcher keeps its `RetryPolicy`, the LLM
adapter its repair retry; a failed agent run fails loudly with its usage logged
(ADR-0018).

## Alternatives considered
- **Three actions per topic** — three approvals on the phone for one idea; Hassan chose
  one.
- **A new thread per revision** — loses the run's history across revisions and needs
  the inbox to link threads; Hassan chose the same thread.
- **Previous draft as a contract field** (`AgentResult.revision_inputs`) — a C1
  contract change so the supervisor could carry agent-specific data. The preview text
  is enough for the writer and keeps the supervisor agent-agnostic.
- **Resume synchronously in the HTTP request** — the phone would wait minutes, and a
  dropped connection could cancel the run mid-way.
- **A separate worker process for revisions** — a queue and a second process before
  C10 (staging); the inbox already runs as the one process that resumes runs.
- **Judge log in `OpsState`** — critic scores are agent-internal and the log must
  outlive threads; a JSONL file joined on action id needs no contract change.

## Consequences
- Good: the first real agent runs end to end on the phone; rejections improve the draft
  without leaving the thread; agreement data accrues from the first draft.
- Bad / cost: the inbox now runs agents (keys and network in that process) and gets a
  background task and a restart sweep; a revision costs a full pipeline run; the lesson
  outline is only as good as the planner's outline.
- Revisit when: C6 creates tasks (replaces `proofline propose`), C9 adds edit, C10 moves
  work to scheduled jobs (revisions may move to a worker), C5 builds the executor table
  (must exclude `approve_draft`).
