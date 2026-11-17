# ADR-0021: Inbound injection suite and budget guard, in `measured_evals`

- Status: proposed (for Hassan's review; GROW-15)
- Date: 2026-11-17
- Phase/Step: Evals and hardening (GROW-15, first part)

## Context
Two hardening gaps were left open on purpose by earlier steps:

1. **Content read from outside.** `checks.injection_check` (C7, ADR-0018) screens what
   the system is about to send out. Nothing screens what comes in: web pages from the
   researcher today, tool results from C5. The repo's rule is that fetched content is
   data, never instructions, and the pipeline wraps and escapes it. That rule is a
   prompt convention; there is no test corpus behind it and no record of what gets
   through.
2. **Spend.** The critic loop has two exits, a score and a revision count, and the
   supervisor has a revision cap. None of them is a cap on money. C10 plans
   scheduled runs "with budget caps", and scheduled means nobody is watching.

Constraints: `measured_evals` stays generic and dependency-free (ADR-0012); no model
is called in the fast suite; nothing here may claim a detection rate it hasn't
measured; `measured_ops` is being changed by C5 and is not touched.

## Decision
1. **A separate inbound screen**, `measured_evals.inbound.inbound_check`, with the
   same shape as every check (`(text, context) -> CheckResult`, a reason that never
   quotes the text). It reuses the outbound patterns for overrides, role tags and
   hidden characters, adds a French override, role-change phrases, a tool-call
   imitation and a Markdown image whose address carries a query, and **drops** the
   outbound rule against links with query parameters, which are normal on a web page.
2. **A flag never grants trust.** Unflagged content is still untrusted data, wrapped
   and never obeyed. A flag lets the caller drop or down-weight a source or show a
   human, and leaves evidence in a trace.
3. **A corpus with its misses in it.** `data/injection/inbound_v0.toml` holds attacks
   and benign near-misses, each with a `today` column: what the screen does with it
   now. A test fails when behaviour differs from that column in either direction.
   Attacks the patterns can't catch stay in the file as known misses.
4. **The corpus is demo data and its counts describe only itself.** On it today:
   12 of 16 attacks flagged, 2 of 7 benign texts flagged. These are not detection
   rates for real content and must not be quoted as such.
5. **A budget guard**, `measured_evals.budget.check_budget(budget, usages, prices,
   next_call=...)`, called before a model call. It raises `BudgetExceeded` when calls,
   tokens or cost, including the planned call, would cross a cap, so the call that
   breaks the budget is never made.
6. **Unknown cost fails closed.** Under a cost cap, a usage with no usable price stops
   the run. A missing price is never 0 (the rule `measured_evals.cost` already has).
7. **Not wired in yet.** Neither piece is called by the pipeline or the supervisor in
   this change. Wiring the screen into the researcher and the guard into the
   supervisor follows C5, as its own change with its own e2e case.

## Alternatives considered
- **Add the inbound rules to `injection_check`** — one check for both directions
  would either flag every source link coming in or stop flagging exfiltration links
  going out. The directions have different base rates.
- **A model-based injection classifier** — better on paraphrases, but it costs a call
  per source, needs a key in CI, and is itself a model reading attacker text. Worth
  measuring later against this corpus; not a v0.
- **Only keep attacks the screen catches** — a suite that always passes teaches that
  injection is handled. The known misses are the most useful rows in the file.
- **Budget as a LangGraph recursion limit** — it counts steps, not tokens or money,
  and a single long call can cost more than many short ones.
- **Enforce the budget after each call** — the overspending call has then been paid for.

## Consequences
- Good: the "fetched content is data" rule has a test corpus; every change to the
  screen shows up as a diff in the `today` column; a run can be stopped on money, by
  code, before the call.
- Bad / cost: patterns miss paraphrased, encoded and authority-claim attacks (four
  known misses in the corpus) and raise false alarms on text that quotes an attack or
  a chat transcript (two in the corpus). The planned call's cost is an upper estimate
  from `max_tokens`, so a run may stop slightly early. Bait in the topic field is not
  caught by this screen.
- Not done here, still open under GROW-15: wiring both into the running system, auth
  and rate limits, a kill switch (C10), and a corpus drawn from real incidents.
- Revisit when: C5 lands tool results (new inbound surface), the model-graded tier
  can score an injection classifier against this corpus, or a real attack is logged.
