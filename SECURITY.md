# Security policy

Proofline is a one-person, built-in-public project. Everything in this repository is
demo config and sample data. Real configuration, policy values and personal data
live in a separate private repository.

## Reporting a vulnerability

Report it privately through GitHub: the repository's **Security** tab, then **Report a
vulnerability**. Please don't open a public issue, pull request or discussion for a
security problem.

Include what you found, how to reproduce it, and what an attacker could do with it.
There is no bug bounty and no fixed response time: one person reads the reports.

## What counts

Reports are most useful when they break one of the rules the design depends on:

- **An action leaves the system without approval.** An action that should wait at
  the approval gate runs without it, or a `never` action is approved or executed
  ([ADR-0014](docs/adr/0014-measured-ops-package-and-approval-ownership.md),
  [ADR-0016](docs/adr/0016-supervisor-graph-and-approval-gate.md)).
- **A failed or missing check lowers a decision** instead of raising it to approval
  ([ADR-0018](docs/adr/0018-eval-guard-node.md)).
- **The inbox's sign-in, session, CSRF or CSP protections can be bypassed**
  ([ADR-0017](docs/adr/0017-approval-inbox.md)).
- **Personal data reaches logs, check reasons, the judge log or traces**, where the
  design says it must not (ADR-0018).
- **Fetched content is followed as instructions** by the pipeline, beyond the known
  limits written down in the ADRs.
- **A secret or personal data in the repository or its history.**

Known limits are already documented. The guard checks and the inbound screen are
pattern heuristics with listed false positives and misses (ADR-0018,
[ADR-0021](docs/adr/0021-inbound-injection-suite-and-budget-guard.md)). A new
bypass of those patterns is still welcome. If it isn't sensitive, an issue proposing
it as a corpus row is the fastest route.

## Deployment assumptions

The approval inbox binds to `127.0.0.1` and is meant to be reached over a private
tailnet with `tailscale serve`, never the public internet ([`docs/INBOX.md`](docs/INBOX.md)).
Running it on a public address is not a supported setup.

## Supported versions

There are no releases yet. Only the `main` branch is supported.
