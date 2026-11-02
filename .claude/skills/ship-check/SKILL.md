---
name: ship-check
description: Definition-of-done gate before declaring a task finished or committing — tests, lint, types, contracts, docs, scope.
disable-model-invocation: true
allowed-tools: Bash(uv run pytest *), Bash(uv run ruff *), Bash(uv run mypy *), Bash(git diff *), Bash(git status *)
---

Run the definition of done and report a checklist. Don't fix things silently —
report, then offer to fix.

1. `uv run ruff check . && uv run ruff format --check .`
2. `uv run mypy`
3. `uv run pytest -m "not live" -q -rxX` — list any XPASS (marker must be removed)
   and remaining xfails for the current week.
4. `git diff HEAD --stat` and read the diff:
   - New behavior has a test? New LLM output has a schema?
   - Prompt files changed → version bumped + CHANGELOG line?
   - New state model → in `checkpointing.STATE_MODELS`?
   - Anything Phase 2/3 (agents, supervisor, MCP in app)?
5. Docs: does `docs/ARCHITECTURE.md` still describe the code? Is the PLAN box ticked?
   Did a decision change architecture/deps/contracts without an ADR?

Output:
```
SHIP-CHECK: pass | fail
[x]/[ ] lint · types · tests · schemas · prompts · serde · scope · docs · ADR
Fix list: ...
Suggested commit: <type>(<scope>): <summary>
```
