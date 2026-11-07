# ADR-0015: Send no temperature by default

- Status: accepted (2026-11-07)
- Date: 2026-11-07
- Phase/Step: before C1 (first live run)

## Context
The first live run failed in the planner with a 400 from the Anthropic API:
`temperature` is deprecated for the current models (`claude-haiku-5-5`,
`claude-sonnet-5-5`). `RunConfig` set a temperature for every role (planner 0.3,
researcher 0.0, writer 0.7, critic 0.0), and ADR-0008 relied on critic temperature 0
for steadier scores.

## Decision
- `ModelSpec.temperature` is optional and defaults to `None`. `ChatAnthropic` drops
  `None` values from the request, so no temperature is sent.
- `RunConfig` sets no temperature for any role.
- The field stays, so a provider or older model that accepts it can still be given one.
- Critic consistency comes from the rubric, anchored scores and the score computed in
  Python (ADR-0008), measured by judge agreement once the golden set exists (GROW-13),
  not from temperature.

## Consequences
- Supersedes the "critic temperature 0" line in ADR-0008.
- Output variance is whatever the model's default is. If it turns out too high for the
  critic, the fix is the rubric and examples, checked with the eval package.
