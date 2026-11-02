# Prompt changelog

One line per change: `date | prompt@version | what | why | evidence`.
Rubric changes (docs/RUBRIC.md) are logged here too, as `rubric@vN`.

| Date | Prompt | What | Why | Evidence |
|---|---|---|---|---|
| 2026-10-20 | planner@1.0.0, researcher@1.0.0, writer@1.0.0, critic@1.0.0, rubric@v1 | Initial versions | Kickoff | — |
| 2026-10-30 | planner@1.1.0 | Copy target seconds from <format>; no longer chosen by the model | Drives target_words and length_fit; now set in code (ADR-0011) | S1 review |
| 2026-10-30 | writer@1.1.0 | Untrusted-content rule for <research_notes>; one id per bracket ([S2][S5]); gaps → "say it's unclear" (no invented "I haven't measured") | Web titles/findings reach the writer outside <source> tags; grouped ids inflated word count; don't put words in the host's mouth | S1 review |
| 2026-10-30 | critic@1.1.0 | Untrusted-content rule; each criterion exactly once in rubric order; 8 = the criterion's anchor (not "ready to review"); length deviation given, not computed; must_fix format matches schema | Injection via research notes; duplicate criteria now fail validation; holistic drift; derived numbers in code (PROMPTING rule 6) | S1 review |
| 2026-11-01 | planner@2.0.0 | Returns an `Outline` (2–4 beats, no target_seconds) for a script under the word limit; brand is a `{brand}` placeholder | Week 1 scope reset to one short format; brand name configurable (ADR-0012) | — |
| 2026-11-01 | writer@2.0.0 | Writes title/hook/body/call to action under `{max_words}`; `{outline}` replaces `{plan}`; `{brand}` placeholder | Same (ADR-0012) | — |
| 2026-11-01 | critic@2.0.0 | Length given as word count vs limit; `{outline}` replaces `{plan}`; `{brand}` placeholder | Same (ADR-0012) | — |
| 2026-11-01 | rubric@v2 | `length_fit` anchors measure the word limit; `voice` refers to the configured brand | Same (ADR-0012) | — |
