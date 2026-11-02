---
name: live-run
description: Run the real pipeline on a topic (paid API calls), then summarize the result — score trajectory, sources, what the critic missed — and log it to docs/run-log.md.
disable-model-invocation: true
argument-hint: "\"topic\" [\"audience\"]"
---

Live run for: $ARGUMENTS

1. Confirm with Hassan before running — this calls Anthropic (and Tavily when
   `PROOFLINE_RESEARCHER=web`).
2. Run `uv run proofline run --topic "<topic>" --audience "<audience>" -v`; after Week 3
   Hassan answers the review prompt himself.
3. Read `outputs/<thread_id>/script.md` and `script.json`.
4. Report in under 20 lines:
   - Score per revision and the criterion that moved most
   - Sources: count, domains, any finding cited without support
   - Word count vs the limit (`PROOFLINE_MAX_WORDS`, default 200)
   - 3 concrete weaknesses a strict editor would flag that the critic didn't
   - LangSmith trace link if tracing is on
5. Append one row to `docs/run-log.md` (create with a header if missing):
   `date | thread_id | topic | researcher | revisions | final score | prompt versions | note`.
