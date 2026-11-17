# Evals: tiers, commands and the baseline

Status: the **deterministic tier** runs in CI (`.github/workflows/evals.yml`). The
golden set it runs on is a **draft** ([`GOLDEN_SET.md`](GOLDEN_SET.md)): no item is
approved, the committed baseline is a placeholder, and no number here measures a
model or the pipeline.

## Two tiers

| Tier | Needs | Runs | Answers |
|---|---|---|---|
| Deterministic | nothing: no model, no key | every push and PR | Is the output shaped right, within the limit, cited to real sources, free of the item's forbidden strings? |
| Model-graded | a model API key (costs money) | only when a key is present | Is it clear, honest about limits, right for the audience? (the `judge` assertions) |

The order matters. A check computed in code gives the same answer every time and
costs nothing, so it runs first and on everything. A model-graded check costs a call
and varies between runs, so it runs second and only on outputs that survived.

## What the deterministic tier checks

On every output (`measured_evals.suites`):

| Check | Fails when | How to fix the output |
|---|---|---|
| `structure` | fewer than three blocks (title, hook, body, call to action), or a citation in the title | separate the parts with blank lines; keep the title clean |
| `word_limit` | more than `max_words` words (default 140; citation marks not counted) | cut; one idea per script |
| `citations_resolve` | a `[S9]` that isn't one of the item's sources | cite only ids that exist |
| `no_uncited_claims` | a body sentence without a citation (only when the item has sources) | cite it or cut it |

Then the item's own `expected` and `must_not` assertions.

`no_uncited_claims` is a heuristic: the body is everything except the title, the
hook and the last block. Code can't tell a claim from a transition, so the rule is
strict on purpose. Failure details name the kind of problem and a count; they never
quote the output.

## Run it

```bash
uv run python -m measured_evals run                      # fake writer, bundled draft set
uv run python -m measured_evals run --writer fake-careless   # a bad fake: must fail
uv run python -m measured_evals run --outputs DIR        # your saved outputs, DIR/<item-id>.md
uv run python -m measured_evals run --approved-only      # only items Hassan approved (today: none)
uv run python -m measured_evals run --baseline evals/baselines/content_v0.placeholder.json
```

Exit code 0 when every item passes the deterministic checks, 1 otherwise.

### The fake writers measure nothing

`measured_evals.fake.grounded_writer` builds a script from the first sentence of
each source, skipping any sentence the injection check flags. It passes 20 of 20 by
construction. That shows the set, the suite and the format agree with each other. It
is a wiring result, not a quality result.

`careless_writer` copies sources whole, obeys the bait, cites `[S9]` and invents a
percentage. It fails 20 of 20, and CI fails if it ever passes: a check that can't
fail isn't a check.

## The baseline

A baseline is the recorded result of a run, kept so the next run has something to be
compared with (`measured_evals.baseline`). Format, JSON:

```json
{
  "schema_version": 1,
  "golden_set": "content-v0",
  "status": "needs_eval",
  "approved_by": null,
  "produced_by": {"generator": "...", "placeholder": true},
  "tolerance": {"deterministic_pass_rate": 0.0, "judge_mean": 0.05},
  "metrics": {
    "deterministic_pass_rate": {"value": 1.0, "status": "needs_eval", "source": "...", "how_to_get_it": "...", "used_in": ["ci-regression-gate"]},
    "judge_mean": {"value": null, "status": "unmeasured", "source": null, "how_to_get_it": "...", "used_in": ["ci-regression-gate"]}
  },
  "items": {"<item-id>": {"value": true, "status": "needs_eval", "source": "...", "how_to_get_it": "...", "used_in": ["ci-regression-gate"]}}
}
```

Every number is an entry with a `status`: `unmeasured`, `needs_eval` or `real`, the
same three states as the reality-inputs files used elsewhere in Hassan's projects.

**A baseline gates nothing until it is real.** `compare()` marks regressions as
enforced only when the file is `real` (with `approved_by`) and every compared entry
is `real` with a `source`. Against a placeholder it prints the differences and
`NOT ENFORCED`, and the build doesn't fail on them.

`evals/baselines/content_v0.placeholder.json` is the only baseline in the repo. It
came from the fake writer. Its values are placeholders, tagged `needs_eval`
(`judge_mean` is `unmeasured`, with no value).

### Making it real (Hassan)

1. Approve golden items (`GOLDEN_SET.md`).
2. Run the real pipeline on the approved items and save each output as `<item-id>.md`.
3. `uv run python -m measured_evals run --approved-only --outputs DIR --write-draft-baseline evals/baselines/content_v0.json`
4. Read the result. If you accept it, set `status` to `real` on the file and on each
   entry, fill `source` (which run) and `approved_by`, and point the workflow at it.

A regression is then: the pass rate drops by more than its tolerance, or an item
that passed in the baseline fails now.
