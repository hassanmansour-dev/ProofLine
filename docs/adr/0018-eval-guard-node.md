# ADR-0018: Eval/guard node v0: placement, fail-closed escalation, usage and cost

- Status: proposed (2026-11-14, built in C7; plan and its four choices approved by Hassan the same day)
- Date: 2026-11-14
- Phase/Step: Phase 1 / C7 (GROW-86)

## Context
C1 gave every `PendingAction` an `eval_scores` field "written only by the eval node
(C7), never by the proposing agent" (ADR-0008, ADR-0014), and `run_agent` already
rejects actions that arrive scored. C7 builds that node: quality, prompt-injection and
PII checks, plus tracing and cost per agent, using `measured_evals`. Open questions:

1. Where the node sits, and where the scored actions live (the agent's
   `AgentResult` is append-only and frozen).
2. How a failed check changes what happens to an action, without ever lowering a
   decision the policy would make on its own.
3. What happens when an action reaches triage with no scores at all.
4. What "quality" means before judge models exist (GROW-14).
5. How usage and cost are counted, including Anthropic prompt-cache tokens.
6. How the inbox shows a failed PII check without showing the personal data.

## Decision

**Graph.** `call_agent → evaluate → triage → gate`. `evaluate` scores every action in
the latest `AgentResult` with `measured_evals.evaluate(action.preview, context)` and
writes copies (`model_copy(update={"eval_scores": ...})`) to a new single-writer state
field, `OpsState.evaluated_actions`. `triage` reads that field, not the raw result. The
agent's `AgentResult` stays exactly as the agent returned it: the audit trail shows
what was proposed and, separately, what the eval node said about it. `evaluate` is the
only code that writes `eval_scores`. The scoring context is the supervisor's
(`build_supervisor(..., eval_context=...)`, e.g. the PII allowlist from private
config), never the agent's.

**Escalation lives in the policy** (`ApprovalPolicy.decide`), next to the high-risk
rule, so there is one place that decides:

1. a `"never"` row → never (no score changes this);
2. `risk_level="high"` → approval;
3. **`eval_scores` missing, or `eval_scores.passed` false → approval** (fail closed);
4. otherwise the table's row (unlisted types → approval).

A failed or missing eval can raise "auto" to "approval" and changes nothing else. It
can never lower a decision. A test checks every combination of row × risk × eval.

**Checks, v0: deterministic only** (no model, no network, no keys; judges in GROW-14).
Each check scores 0 or 1 and gives a short reason that never contains matched text:

- `pii`: emails, phone numbers (North American and international), SINs and card
  numbers (both Luhn-checked). Unlike `scripts/pii_scan.py`, fictional values are
  flagged too: anything leaving the system counts. Exact addresses can be allowed per
  call (`context["pii_allow"]`), from private config.
- `injection`: instruction-like text ("ignore previous instructions", role and system
  tags, a stray `</source>`), invisible or bidi control characters, and links with
  query parameters (a way to carry data out). It will flag source links in content
  drafts; that only raises them to approval, by design. A heuristic net, not a
  classifier: the injection suite is GROW-15.
- `quality`: structure only: not empty, within `context["max_words"]`, no leftover
  placeholders, citations present when `context["require_citations"]`. Rubric quality
  stays with the critic (C8) and judges (GROW-14).

`EvalScores` gains `reasons` (metric → short text). A check that raises fails the node
loudly; it is never turned into a silent score.

**No matched PII value is stored or shown anywhere**: not in `EvalScores`, the inbox,
logs, traces or the gate payload. Reasons name the kind ("email found"), never the
value. A test captures logs and the payload to check.

**Usage and cost.** `measured_evals.Usage` (agent, model, input tokens, output
tokens, cache-read tokens, cache-write tokens at 5 minutes and at 1 hour) and
`cost(usage, prices)`. Cache reads and writes are counted separately from normal input
because they're priced differently. A model missing from the price table gives
`None` ("unknown"), never 0. Demo prices ship in
`measured_ops/config/model_prices.demo.toml`; real ones go in private config if they
ever differ. A LangChain callback handler, passed to each agent run by `run_agent`,
collects usage from every LLM call inside the agent's subgraph into a new append-only
field, `OpsState.usage`. `run_agent` also names the run `agent:<name>` with tag and
metadata (`task_id`, `agent`), so LangSmith groups traces per agent when it's on.

**Inbox.** Each waiting action shows its checks (pass or fail, with the reason) and
the run's tokens and cost per agent. The list marks actions with a failed check. The
gate payload carries the same JSON-safe `eval` block.

**State.** `OpsState` gains `evaluated_actions` (no reducer, one writer: `evaluate`)
and `usage` (append-only). `Usage` joins `STATE_TYPES`, which builds the serde
allowlist (ADR-0006).

## Alternatives considered
- **Score inside `triage`** — one node fewer, but the eval step is the point of C7
  and needs its own retry policy once it calls judges; and triage logs, so it must
  stay cheap and deterministic.
- **Escalate in `triage` instead of the policy** — two places would decide; the
  "never lower" property is easier to prove with everything in `decide`.
- **Missing scores = no signal** — an action that skipped the eval node would pass
  silently. Fail closed matches unknown action types defaulting to approval.
- **LLM judge for quality in v0** — costs money and keys in CI, and its agreement
  with Hassan isn't measured yet (GROW-13/14).
- **Share patterns with `scripts/pii_scan.py`** — different jobs: the repo scanner
  must tolerate demo data, the guard must not. Kept separate.
- **Usage reported by agents in `AgentResult`** — agents would have to be trusted to
  report their own cost, the same problem as self-scoring.

## Consequences
- Good: every action carries independent scores before triage; a guard failure can
  only add a human check; cost per agent is visible where decisions are made.
- Bad / cost: "auto" actions with links (sourced drafts) now wait for approval; the
  heuristic checks will have false positives. `OpsState` grows two fields, so runs
  paused before C7 have no `evaluated_actions`: the inbox still reads them (the gate
  holds the action), and new runs use the new path.
- Revisit when: judges arrive (GROW-14), the injection suite lands (GROW-15), or a
  budget cap needs cost before the run ends (C10).
