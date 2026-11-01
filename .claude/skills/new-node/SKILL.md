---
name: new-node
description: Implement a graph node end to end following the repo contract — test first, prompt, node, wiring, docs. Use for planner, researcher, writer, critic, human_review, finalizer.
disable-model-invocation: true
argument-hint: "[node-name]"
---

Implement the **$ARGUMENTS** node.

1. **Contract.** Read the node's row in `docs/ARCHITECTURE.md` §3–4 and the stub
   docstring in `packages/proofline/src/proofline/nodes/$ARGUMENTS.py`. List: fields read, fields
   written, schema returned, failure behavior. If anything is ambiguous, ask
   the `langgraph-architect` agent, then Hassan if still unclear.
2. **Tests first.** Find the node's `xfail(strict=True)` tests in `packages/proofline/tests/`. Add any
   missing cases with the `test-engineer` agent (fakes only). Run them: they must fail.
3. **Prompt** (LLM nodes only). Review `packages/proofline/src/proofline/prompts/$ARGUMENTS.md`
   with the `prompt-engineer` agent against `docs/PROMPTING.md`. Bump version +
   CHANGELOG if it changes.
4. **Implement** in `packages/proofline/src/proofline/nodes/$ARGUMENTS.py`:
   `async def $ARGUMENTS(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]`.
   Dependencies only from `runtime.context`. Return only owned fields. Log one
   INFO line with duration + key numbers.
5. **Wire** in `graph.py` if not already (edges per ARCHITECTURE §2, path maps,
   RetryPolicy on network nodes). New Pydantic type in state ⇒ add to
   `checkpointing.STATE_MODELS`.
6. **Green.** Remove the `xfail` markers that now pass. `uv run pytest -m "not live" -q`,
   `uv run mypy`.
7. **Review.** `code-reviewer` agent on the diff; fix blockers.
8. **Docs.** Update ARCHITECTURE if reality changed; tick the PLAN checkbox.
9. Propose a commit message (`feat($ARGUMENTS): ...`) and wait for Hassan.
