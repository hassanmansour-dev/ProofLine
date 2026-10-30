# ADR-0011: Week 1 contract lock: derived values in code, schema-level critic validation, explicit search retry

- Status: proposed
- Date: 2026-10-30
- Phase/Week: P1W1

## Context
The Week 1 S1 review (`langgraph-architect` + `prompt-engineer` on the kickoff
contracts) found five places where the contracts would fail on the first live run
or let a buggy node pass its tests. Each was checked against the installed
packages (langgraph 1.2.16, langchain-anthropic 1.7.6, tavily-python):

1. **Structured output on Sonnet 5.5.** `with_structured_output` defaults to tool
   calling; `langchain_anthropic._supports_forced_tool_choice` is False for
   `claude-sonnet-5-5` (writer, critic), so the tool call is unforced. A prose reply
   raises `OutputParserException` outside `include_raw`, skipping the repair retry,
   and the repair turn (assistant `tool_use` + plain human message) is a shape the
   API rejects.
2. **Critique with a repeated criterion** passed validation (`min/max_length=8`
   only), then `overall` raised `KeyError` in the router.
3. **`Draft.cited_source_ids` was model-reported** and could disagree with the
   narration: the writer filtered the list but left `[S42]` in the spoken text, and
   the final Sources section trusted the list. `[S2, S5]` citations (the format
   `notes_block` shows) weren't stripped from the word count, nor was the space
   before a citation (`"step [S1]."` counted `"."` as a word).
4. **`Plan.target_seconds` was model-reported** ("unless the topic clearly needs
   less") though it drives `target_words` and `length_fit`.
5. **Researcher retries.** LangGraph's `default_retry_on` returns False for
   `RuntimeError`/`ValueError`/`OSError` and True for most other exceptions, so
   "all queries failed" was never retried while an invalid Tavily key would be.

## Decision
- The LLM adapter calls `with_structured_output(schema, include_raw=True,
  method="json_schema")`.
- `Critique` has a model validator: every criterion exactly once. A bad critique
  fails inside parsing, so the adapter's repair retry handles it; nodes don't
  re-check.
- `Draft` has no `cited_source_ids` field. It is a property parsed from the spoken
  text with `schemas.CITATION_RE` (`[S3]` or `[S2, S5]`), in first-seen order. The
  writer strips inline citations to unknown ids from the narration (logged at
  WARNING). `word_count` removes citations and the whitespace before them.
- The planner overwrites `plan.target_seconds` with `TARGET_SECONDS[video_format]`.
- `search.SearchUnavailableError` marks transient search failures (timeout,
  transport error, 5xx); the Tavily adapter maps those to it and lets auth, quota
  and bad-request errors through. The researcher raises it when every query
  failed transiently, else re-raises the first non-transient error.
  `graph.py` wires the researcher with `researcher.RETRY_POLICY`
  (`RetryPolicy(max_attempts=3, retry_on=SearchUnavailableError)`).
- Unit tests run nodes through a one-node graph compiled with the strict-serde
  `InMemorySaver`, and `FakeLLM` records the `run_config` each call received.

## Alternatives considered
- **Keep the uniqueness check in the critic node (as the stub said)** — a bad
  output kills the run with no repair attempt.
- **Clamp `target_seconds` instead of overriding it** — keeps a model-sourced number
  in a derived value for no benefit.
- **Keep `cited_source_ids` in the schema and reconcile it with the narration** —
  two sources of truth for one fact; the narration is what gets spoken.
- **Default `retry_on`** — retries the wrong failures (see Context 5).

## Consequences
- Good: every derived number is computed in code (ADR-0002, PROMPTING rule 6);
  citations in the script and the Sources section can't disagree; one bad LLM
  output gets one repair attempt instead of failing the run; quota and key errors
  fail fast.
- Bad / cost: `Draft` is a breaking change for any checkpoint holding the old
  shape. None exist yet (no live runs), which is why it's done now.
- Revisit when: Week 3 (checkpointer in use), together with the open S1 items
  below, which need Hassan's call:
  - **Resume reproducibility.** Prompts, rubric and brand voice are read from disk
    at call time; `FinalScript.prompt_versions` records the versions at finalize
    time, not the ones used. Proposal: snapshot `prompt_versions` into `RunConfig`
    at run start.
  - **Checkpoint compatibility.** jsonplus falls back to `model_construct` when a
    stored model no longer validates, leaving nested dicts. Proposal: any state
    model change ships with a test that loads an old checkpoint (amends ADR-0006).
  - **`approved` after a human reject** stays False through later critic-loop
    rewrites, so those are labelled "human feedback" in history.
