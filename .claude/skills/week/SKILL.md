---
name: week
description: Start or resume a step of the Proofline / Measured Ops plan (e.g. C1, C8) — loads the step's scope and dependencies, checks what's done, and proposes the next session's work.
disable-model-invocation: true
argument-hint: "[step, e.g. C1]"
---

Start work on **step $ARGUMENTS** of `docs/PLAN.md`.

1. Read `docs/PLAN.md`. If the `◀ CURRENT` marker isn't on step $ARGUMENTS, ask
   Hassan whether to move it, and move it only after he says yes. If a step it
   waits on isn't done, say so before anything else.
2. Read the step's checklist, "Done when", and concepts. Read the ADRs and
   `docs/ARCHITECTURE.md` sections the step touches.
3. Find what's already done: ticked boxes, `git log --oneline -15`, and which
   `xfail(strict=True, reason="$ARGUMENTS target")` tests still exist
   (`uv run pytest -m "not live" -q -rx`).
4. Create a task list for the **next unfinished session only** (not the whole step):
   test(s) to make pass → implementation steps → review → ship-check.
5. Show Hassan, in under 15 lines: where the step stands, the next session's
   tasks, the one LangGraph concept this session teaches, and any decision he
   needs to make first. Then wait for his go.

Don't write code in this command. Scope outside step $ARGUMENTS needs his OK.
