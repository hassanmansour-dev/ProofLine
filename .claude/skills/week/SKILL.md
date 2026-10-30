---
name: week
description: Start or resume a week of the Proofline plan — loads the week's scope, checks what's done, and proposes the next session's work.
disable-model-invocation: true
argument-hint: "[week-number 0-4]"
---

Start work on **Week $ARGUMENTS** of `docs/PLAN.md`.

1. Read `docs/PLAN.md`. If the `◀ CURRENT` marker isn't on Week $ARGUMENTS, ask
   Hassan whether to move it, and move it only after he says yes.
2. Read the week's goal, sessions, "Done when", and concepts. Read the ADRs and
   `docs/ARCHITECTURE.md` sections the week touches.
3. Find what's already done: ticked boxes, `git log --oneline -15`, and which
   `xfail(strict=True, reason="Week $ARGUMENTS target")` tests still exist
   (`uv run pytest -m "not live" -q -rx`).
4. Create a task list for the **next unfinished session only** (not the whole week):
   test(s) to make pass → implementation steps → review → ship-check.
5. Show Hassan, in under 15 lines: where the week stands, the next session's
   tasks, the one LangGraph concept this session teaches, and any decision he
   needs to make first. Then wait for his go.

Don't write code in this command. Scope outside Week $ARGUMENTS needs his OK.
