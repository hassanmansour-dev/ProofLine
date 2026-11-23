# Contributing to Proofline

Proofline is built in public by one person, following an ordered plan
([`docs/PLAN.md`](docs/PLAN.md)). Issues are welcome: bugs, unclear docs, a check
that misses something. For anything beyond a small fix, open an issue before a pull
request. The plan runs one step at a time, and work outside the current step may not
fit yet.

Security problems: see [`SECURITY.md`](SECURITY.md), not an issue.

## Set up

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync
uv run pre-commit install        # gitleaks + PII scan on every commit
```

## Before you open a pull request

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict, on every package
uv run pytest -m "not live"      # no keys, no model calls
```

CI runs the same checks, plus a secret scan over the full history, a PII scan over
every tracked file, the supervisor's end-to-end test against Postgres, and each lower
package installed on its own.

## Rules

- **This repository is public.** No secrets, no personal data, no real calendar,
  prospect or learner data. Demo config and sample data only. The pre-commit hooks
  and CI check for secrets and personal data, but they are a net, not a guarantee.
- **Dependencies point one way:** `proofline` → `measured_ops` → `measured_evals`.
  `measured_evals` never imports the other two, and `measured_ops` never imports
  `proofline`. Boundary tests enforce both.
- **Tests never call a model or the network.** Use the fakes in
  `packages/proofline/tests/fakes.py`. Anything that needs a key is marked
  `@pytest.mark.live`.
- **Derived numbers are computed in code**, never taken from a model's output.
- **Prompts are versioned files.** Changing one means bumping its `version:` and
  adding a line to `packages/proofline/src/proofline/prompts/CHANGELOG.md`.
- **A change to architecture, a dependency or a contract needs an ADR** in
  [`docs/adr/`](docs/adr/) (template: `0000-template.md`). Don't contradict an
  accepted ADR silently: propose a new one.
- **No claim without a measurement.** A number in the docs comes from a command or a
  file you can point to. If only a paid run could produce it, write that it is not yet
  measured.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(critic): ...`, `docs: ...`), one logical change each.

## Licence

Proofline is licensed under the [Apache License 2.0](LICENSE). Under section 5 of the
licence, a contribution you submit is licensed under the same terms.
