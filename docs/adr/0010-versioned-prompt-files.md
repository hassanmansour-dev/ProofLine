# ADR-0010: Prompts are versioned Markdown files

- Status: accepted
- Date: 2026-10-20
- Phase/Week: P1W1

## Context
Prompt changes are the most frequent and least reviewed change in LLM systems.
Phase 3 needs regression checks keyed by prompt version.

## Decision
- One file per node in `src/proofline/prompts/<node>.md` with YAML
  frontmatter: `name`, `version` (semver), `model_role`, `description`.
- Body has a `## system` and a `## user` section; the user section uses
  `{placeholders}` filled by the loader (`str.format_map` with a strict map
  that raises on missing keys).
- Changing a prompt = bump version + line in `prompts/CHANGELOG.md`.
- Every LLM call and every run's metadata records the prompt versions used.

## Alternatives considered
- **Inline f-strings** — invisible in review, no versioning.
- **LangSmith prompt hub** — nice later; keeps prompts out of git review for now.

## Consequences
- Good: prompt diffs are reviewable; traces and evals can be grouped by version.
- Bad: one more file per node.
