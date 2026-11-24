# ADR-0022: MCP tools with a read/write split, action params, and an execute node

- Status: proposed (for Hassan's review of the C5 PR)
- Date: 2026-11-23
- Phase/Step: Phase 1 / C5 (GROW-84)

## Context
C5 gives Measured Ops its first tools: Jira (the GROW project) and a personal calendar,
over MCP. docs/PLAN.md and GROW-84 set the rules: reads run freely; every write goes
through the approval policy (calendar blocks need approval, per C3; ticket triage is
auto); `approve_draft` never gets an executor (ADR-0019); the executor handles a revision
re-proposing an action that was already approved (ADR-0019); real credentials only in the
environment or measured-ops-private; tests use fakes. Done when: an agent reads GROW,
proposes a ticket move, and approval applies it (e2e).

What the contract had: `PendingAction(type, target, preview, ...)` and `OpsState.ready`,
which nothing consumed ("runs from C5", ADR-0016). Open questions:

1. How an executor knows what to run. `preview` is text for a human and `target` a short
   string; a ticket move needs an issue, a status, labels.
2. How to stop an agent showing the human one thing and running another.
3. How agents read without being able to write.
4. Where execution sits in the graph, given `interrupt()` re-runs its node and a crash can
   land between a write and its checkpoint.
5. Which MCP servers and tool names. Every server names its tools and arguments its own
   way, and nothing here can reach Hassan's accounts to check them.

Found while building, on `mcp` 2.3.0 (the current SDK; `langchain-mcp-adapters` 0.3.2
still requires `mcp<2`): `mcp.Client` takes a URL, `StdioServerParameters` or an
in-process `MCPServer`, so tests can run the real client path against demo servers; a
stdio server gets only a safe default environment plus the variables passed; transport
failures arrive as nested `ExceptionGroup`s; and the streamable-HTTP client reports any
4xx/5xx as one generic MCP error (-32603), dropping the status.

## Decision

**1. `PendingAction.params`** (`dict[str, JsonValue]`, default empty): the executor's
structured arguments. Empty for types nothing executes, so every existing action and
checkpoint stays valid.

**2. Executable types are specs** (`measured_ops.tools.specs`, pure, no MCP import):
`triage_ticket` (move, labels, priority on one issue) and `schedule_calendar_block` (one
block on one configured calendar). A spec has a Pydantic params model and renders the
action's `target` and `preview` from the params. Agents propose with `spec.propose(task_id,
params)`. The supervisor's `evaluate` node re-checks every executable action
(`spec.check`): params valid, target and preview equal to what the params render, and in
scope (configured Jira projects, configured calendar aliases). A mismatch, a scope breach,
or an executable type with no tools configured raises `AgentContractError` before anything
is scored or shown. **What the human approves is exactly what runs.**

**3. Read/write split by construction.** Agents get read ports (`JiraReads`,
`CalendarReads`) through their constructor; write ports exist only inside the executors
the supervisor holds. Each MCP client has an allowlist of tool names; the read client's
is the config's read tools, so a write raises `ToolNotAllowedError` before anything is
sent. The config refuses a tool listed as both read and write. Reads are scoped as well:
Jira search results outside the configured projects are dropped (the JQL scope can be
escaped; the filter is the guard), and calendars are addressed by alias, so ids stay in
config.

**4. An `execute` node.** `call_agent → evaluate → triage → (execute | gate | END)`;
`gate → (execute | gate | revise | END)`; `execute` routes like `gate`. It runs **one**
ready action per step (the first without an outcome) and appends an `ActionOutcome` to a
new append-only field, `OpsState.executed`:
- types without a spec get `"manual"`: nothing runs (`approve_draft`, and any type with no
  executor). `Executors` refuses an executor for `approve_draft`;
- an executable type with no configured executor raises `ToolConfigError`: an approved
  ticket move is never silently recorded as manual;
- `"applied"` or `"already_applied"` otherwise. Executors read before they write and write
  only what differs (triage re-reads afterwards and raises if it didn't take); a change
  with the same identity key already applied in the run (a revision re-proposing an
  approved action) is `"already_applied"` without a tool call;
- a failure raises and stops the run at `execute`, with the decision saved. A
  `RetryPolicy` retries `ToolUnavailableError` (unreachable, timeout, connection closed,
  HTTP 5xx/429) three times; nothing else is retried automatically.
`ActionOutcome` joins `STATE_TYPES` (serde allowlist).

**5. The inbox continues through `execute`.** After committing the gate step, `decide`
runs `ainvoke(None, interrupt_before=["revise"])` (safe: no interrupt is pending), so an
approved action runs in the request (seconds) and a revision still goes to the background.
A failed execution is logged, the run is "stopped" (Retry continues it), and the page says
so. A run waiting to execute an action a human approved counts as stopped at once after a
restart. `create_inbox_app` and `serve` take `executors`; `serve` builds them from
`MEASURED_OPS_TOOLS_CONFIG` (none when unset).

**6. Tools config, in TOML, per deployment.** `measured_ops/config/tools.demo.toml` maps
each logical operation (`search`, `get_issue`, `transitions`, `transition`, `update`,
`list_events`, `create_event`) onto a tool: its name, how the logical arguments are named
(dotted names build nested objects), constant arguments, and where results are (dotted
paths). Servers are stdio (command, args, `env_from`: env var **names** passed through) or
streamable HTTP (https, `headers_from_env`). No secret is ever written in a config. The
real config lives in measured-ops-private. `python -m measured_ops.tools check` verifies a
config against each server's own `tools/list` (tools exist, arguments are parameters,
required parameters covered, read tools not marked as writing), calling no tool.
The demo config points at bundled demo servers (`measured_ops.tools.demo_servers`, a
Jira-like tracker and a calendar over one JSON file), so the public demo runs end to end.

**7. Dependency.** `mcp>=2.3,<3` as an optional `tools` extra on `measured-ops` (and in the
workspace's dev group). The contracts, policy, supervisor and executor layer import
without it (a boundary test pins it). Errors are classified from the SDK's exceptions,
flattening exception groups, and from HTTP statuses recorded by an `httpx2` response hook.
A server's own error text is PII-redacted (`redact_pii`) and cut to 200 characters.

**8. Policy.** The demo table gets `schedule_calendar_block = "approval"` (C3) and drops
`schedule_internal_block`, which nothing used: one calendar action name in both repos.
`triage_ticket` stays `"auto"`.

## Alternatives considered
- **Agents call write tools and the tool layer intercepts them** — every agent would hold a
  write-capable client and the safety would rest on an interceptor; proposing a
  `PendingAction` is already the contract (ADR-0014).
- **Executors parse `target`/`preview`** — text written for a human becomes an API;
  fragile, and the agent controls it.
- **Trust the agent's preview** — a buggy or injected agent could show "add a label" and
  run "move to Done". Rendering the preview from params and re-checking costs a few lines.
- **Execute every ready action in one node step** — one failure would lose the outcomes of
  the actions before it, and a retry would re-run them.
- **Execute inside `gate`** — side effects in the node `interrupt()` re-runs (ADR-0016).
- **`langchain-mcp-adapters`** — LangChain tool wrappers are useful when an LLM picks
  tools (C6/C11 may want them over the read ports), but they pin `mcp<2` today, and the
  executors call fixed tools with fixed arguments.
- **One Python profile per real MCP server** — needs code changes to switch servers, and
  the tool names couldn't be verified here. A mapping in config plus `tools check` keeps
  the choice and the verification with Hassan.
- **A long-lived MCP session per server** — fewer handshakes, but a session to keep alive
  in a long-running inbox; a session per call is simpler at a few calls per action.

## Consequences
- Good: the first actions with real effect run only through checked, scoped, idempotent
  executors; agents can't write; what's approved is what runs; a down tool stops a run
  visibly and Retry is safe; the public demo works without accounts.
- Bad / cost: a contract field and a state field (old checkpoints load: both default empty;
  a pre-C5 run resumed after upgrade gives its earlier `ready` actions "manual" outcomes,
  harmlessly); a session handshake per tool call; the config mapping is a small language of
  its own; the calendar's idempotency matches on title, start and end, so a block renamed
  by hand would be created again; tool results are a new inbound surface (ADR-0021's
  screen isn't wired to them yet).
- Needs Hassan: the real tools config in measured-ops-private (which Jira and calendar MCP
  servers, their tool names, the env vars holding credentials, the personal calendar's id),
  checked with `python -m measured_ops.tools check --config <path>` on his machine.
- Revisit when: C6 needs reads as LLM tools (wrap the read ports, not the raw client);
  a third executable type arrives; ADR-0021's inbound screen is wired to tool results;
  C10 moves execution to a worker; a server needs OAuth (a token in an env var works for
  HTTP today; interactive OAuth does not).
