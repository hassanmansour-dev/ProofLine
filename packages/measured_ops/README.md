# measured-ops

The Measured Ops layer of [Proofline](../../README.md): the contract every agent
implements, the approval policy and, from step C2, the supervisor graph with its
human approval gate (ADR-0013, ADR-0014).

- `contracts.py`: `Task`, `AgentResult`, `PendingAction`, `ApprovalDecision` and
  the supervisor's `OpsState`
- `agent.py`: the agent interface. Every agent is a compiled LangGraph subgraph
  whose input is `{"task": Task}` and whose output is `{"result": AgentResult}`
- `policy.py` and `config/approval_policy.demo.toml`: which action types run
  automatically and which wait for a human. Unknown action types wait for a human

Dependencies point one way: `proofline` → `measured_ops` → `measured_evals`. This
package never imports `proofline`; agents are handed to the supervisor, not imported
by it. The policy file here is a **demo**. Real values live in the private
`measured-ops-private` repo and are passed to `load_policy(path)`.
