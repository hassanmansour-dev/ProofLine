# ADR-0014: Measured Ops lives in `packages/measured_ops`; the supervisor owns approval

- Status: accepted (2026-11-08, Hassan's go on the C1 plan)
- Date: 2026-11-08
- Phase/Step: Phase 1 / C1 (GROW-80)

## Context
ADR-0013 made Measured Ops (a LangGraph supervisor with specialist agents and a
human approval gate) Proofline's v1 scope and left two questions to C1:

1. Where the Measured Ops code lives in this workspace, and how it splits between
   this public repo and the private `measured-ops-private` repo.
2. What happens to the content pipeline's own approval pieces, all still stubs:
   the `human_review` interrupt node (ADR-0005), the planned `/runs` endpoints in
   `api.py`, and the CLI review prompt. The supervisor's approval gate (C2) and the
   approval inbox (C4) cover the same job for every agent.

C1 also fixes the contract every later step builds on: `Task`, `AgentResult`,
`PendingAction`, `OpsState`, the agent interface, the approval policy table and the
`measured_evals.evaluate()` interface.

## Decision

**1. A third workspace member, `packages/measured_ops` (PyPI name `measured-ops`).**
It holds the contracts (`contracts.py`), the agent interface (`agent.py`), the
approval policy (`policy.py` + `config/approval_policy.demo.toml`) and, from C2,
the supervisor graph and its gate.

Dependencies point one way: **`proofline` → `measured_ops` → `measured_evals`**.
- `measured_ops` never imports `proofline`. Agents are handed to the supervisor
  (`build_supervisor(agents, policy)` in C2), not imported by it.
  `packages/measured_ops/tests/test_ops_boundary.py` enforces this.
- `measured_evals` imports neither (its boundary test now checks both).
- `proofline` takes the `measured-ops` dependency in C8, when its pipeline is
  wrapped as the content agent. Until then nothing in `proofline` needs it.

**2. Public/private split.** This repo holds the contracts, the supervisor, the
inbox, agents with demo config and sample data, and the **demo** policy table.
`measured-ops-private` holds the real policy table (passed to
`load_policy(path)`), real config, tuned prompts and personal data, and depends on
these packages. No code path here reads a private file by default.

**3. The contract.**
- `OpsState` is a `TypedDict` with Pydantic values, as ADR-0002 set for
  `ScriptState`: `task`, `results` (append reducer), `pending_actions` (replaced by
  the gate), `decisions` (append reducer). Every value type is listed in
  `contracts.STATE_TYPES`, from which the checkpointer's serde allowlist is built
  (ADR-0006); a strict-msgpack round-trip test pins it.
- `Task`, `AgentResult`, `PendingAction` (type, target, preview, eval scores, risk
  level) and `ApprovalDecision` are frozen Pydantic models that forbid unknown
  fields. `ApprovalDecision` is the gate's resume value; it is in the contract now
  because `OpsState.decisions` records it.
- **Agent interface:** an agent has a `name` and `build()` returning a compiled
  `StateGraph` declared with `input_schema=AgentInput` (`{"task": Task}`) and
  `output_schema=AgentOutput` (`{"result": AgentResult}`). Its internal state stays
  private. `check_agent_graph` rejects a graph with any other input or output keys;
  `run_agent` rejects a result for another task or agent. Agents only *propose*
  actions (`AgentResult.actions`); they never execute anything that leaves the system.
- **Approval policy** is a TOML table (`version = 1`, `[actions]` rows of
  `"auto"` or `"approval"`), read with the standard library's `tomllib` (no new
  dependency). Two rules no table overrides: an unlisted action type needs
  approval, and so does any action with `risk_level = "high"`.
- **`measured_evals.evaluate(output: str, context: Mapping | None) -> EvalScores`**,
  async (real metrics call judge models). `EvalScores` is a frozen stdlib dataclass
  (scores in [0, 1], a threshold, the evaluator's name), so `measured_evals` keeps
  zero runtime dependencies. `passed` is computed from the scores, and an empty
  result never passes. The C1 stub returns an empty result, so nothing gated on it
  can pass before GROW-14.

**4. Approval belongs to the supervisor, not to the content pipeline.**
- `nodes/human_review.py`, `routing.route_after_human`, `HumanDecision`,
  `StartRunRequest`, `RunView`, the `approved` state field and the `"human"` kind
  of `RevisionRecord` are removed. `route_after_critic` now ends at `"finalizer"`.
  `api.py` keeps only `/health` until C4 decides where the inbox app lives. The
  CLI gets no review prompt.
- A rejection doesn't loop inside an agent: the inbox records an
  `ApprovalDecision`, and a rejection with feedback becomes a **new `Task`** for
  the same agent with `feedback` set and `parent_id` pointing at the task it
  revises. The content pipeline keeps `feedback` as an *input* field; the writer
  revises with it as before.
- The `C2 target` tests: `test_reject_then_approve` (proofline graph) and
  `test_run_review_complete_flow` (API) are deleted. The resume-after-restart case
  moves to `packages/measured_ops/tests/test_ops_e2e.py` as the supervisor's
  `C2 target` e2e test.
- ADR-0005 (human review interrupt in a pipeline node) is superseded by this ADR.
  Its rule still applies to the supervisor's gate in C2: nothing with side effects
  before `interrupt()`.

## Alternatives considered
- **Measured Ops as a module inside `proofline` (`proofline.ops`).** Less setup,
  but the orchestrator and one of its agents would share a package. Every later
  agent would then depend on the content pipeline, and nothing would stop the
  supervisor importing it.
- **Supervisor depends on agents (`measured_ops` → `proofline`).** Every new agent
  would change the supervisor's dependencies, and the private repo couldn't add its
  own agents without editing this one. Injection keeps the supervisor closed.
- **Keep `human_review` inside the content agent as a second gate.** Two approval
  paths, and the content agent would interrupt inside a subgraph the supervisor
  is already gating. One gate, one inbox.
- **Agents share `OpsState` keys (add the subgraph directly as a node).** Couples
  every agent to the supervisor's state shape. Separate input/output schemas keep
  each agent's state private.
- **YAML policy file.** Needs a new runtime dependency; TOML is in the standard
  library and reads just as well for a flat table.
- **Pydantic `EvalScores` in `measured_evals`.** Fine technically, but it would give
  the standalone package its first runtime dependency for one small model.

## Consequences
- Good: one approval path for every agent; agents are testable alone through
  `run_agent`; the private repo can add agents and swap the policy without forking
  this one. `measured_evals` stays dependency-free.
- Bad / cost: a third package to version. The content pipeline loses its
  standalone review flow; until C2 there is no approval path at all (nothing
  leaves the system before then anyway). Any checkpoint holding the removed
  `approved` field or `"human"` history records won't load (none exist).
- Revisit when: an agent needs to propose an action that can't be expressed as
  type/target/preview, or the policy needs conditions beyond type and risk level
  (for example, eval scores below threshold forcing approval, likely in C7).
