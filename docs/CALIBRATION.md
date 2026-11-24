# Critic calibration: does the critic agree with a person?

Status: **the tool exists; no calibration has been run.** There is no agreement
number for Proofline's critic yet. The only scores in the repo are a test fixture
labelled synthetic (`packages/measured_evals/tests/fixtures/synthetic_critic_scores.json`),
hand-typed to exercise the arithmetic. Golden run 1 (2026-11-24) produced real critic
scores for the 20 approved items ([`evals/runs/golden-run-1.md`](../evals/runs/golden-run-1.md));
no person has scored those outputs yet, so there is still nothing to compare.

- Code: `measured_evals.calibration`
- Tests: `packages/measured_evals/tests/test_calibration.py`

## Why calibrate

The critic's score decides whether a draft is revised or finalized. If the critic
passes drafts a person would send back, the loop exits early and weak drafts reach
the approval inbox. If it fails drafts a person would accept, every run pays for
revisions that change nothing. A critic nobody has compared with a person is an
opinion with a decimal point.

## The procedure

```bash
# 1. Make the sheet from 20 saved outputs and the critic's scores for them
uv run python -m measured_evals calibration-sheet \
    --outputs DIR --critic-scores critic.json --out-dir outputs/calibration

# 2. Read calibration_sheet.md, type a score per row into calibration_sheet.csv

# 3. Compare
uv run python -m measured_evals calibration-agreement \
    --sheet outputs/calibration/calibration_sheet.csv \
    --critic-scores critic.json --threshold 8
```

`DIR` holds one `<item-id>.md` per output, the same layout `run --outputs` reads.
`critic.json`:

```json
{
  "source": "which run produced these",
  "scale": [1, 10],
  "scores": {"loop-needs-exit": 8.4, "another-item": {"overall": 7.1}}
}
```

Outputs and scores must cover exactly the same ids. `outputs/` is git-ignored: a
filled sheet is the reviewer's data and stays out of this public repo.

The sheet has four columns: `item_id`, `words`, `human_score` (blank), `notes`
(blank). The Markdown copy quotes every output in full for reading. Only the CSV
is read back.

## Why the sheet is blind

By default the sheet leaves the critic's scores out. A reviewer who sees "8.4"
before reading tends to land near 8.4. That is anchoring, and it would raise the
agreement without the critic being any better. `--show-critic` adds the column for
a second pass, after the blind scores are saved, to study disagreements. Don't
report a number from a sheet that showed the scores.

## The metric: Cohen's kappa on pass/fail at the threshold

**What is compared.** Each score becomes a decision: pass if it is at or above the
threshold (Proofline's is `PROOFLINE_SCORE_THRESHOLD`, default 8.0), fail otherwise.
Twenty items give four counts:

| | Person passes | Person fails |
|---|---|---|
| **Critic passes** | both pass | critic only: would ship work the person would send back |
| **Critic fails** | person only: revisions nobody needed | both fail |

**Why decisions and not the raw scores.** The pipeline only ever uses the score as
a decision. A critic that says 6 where the person says 7 changes nothing; one that
says 8 where the person says 7 ships a draft.

**Why kappa and not plain agreement.** Compare two critics on the same 20 drafts:

| | Plain agreement | What happened |
|---|---|---|
| A critic that passes everything, a person who passes 18 of 20 | 0.90 | The critic told nothing apart |
| A critic that matches the person on 18 of 20, with both passing about half | 0.90 | The critic tracks the person |

Plain agreement can't separate them. Kappa subtracts the agreement two raters with
these pass rates would reach by chance:

```
kappa = (observed - expected) / (1 - expected)
expected = critic_pass_rate * person_pass_rate + critic_fail_rate * person_fail_rate
```

1 is perfect agreement, 0 is what chance would give, below 0 is worse than chance.
The first critic above gets 0; the second about 0.8.

**Why not correlation.** A critic that is two points generous on every draft
correlates perfectly with the person and still passes drafts the person fails.

**What goes with it.** Kappa has no direction, so the report also prints the mean
difference `critic - person` (positive: the critic is generous) and the mean
absolute difference, on the raw scores.

**When kappa is undefined.** If both raters put every item on the same side of the
threshold, there is nothing to tell apart and the formula divides by zero. The
tool reports `undefined` with the reason, never 0 or 1. The fix is a sheet with
weaker drafts in it, not a different formula.

## Reading the result, and improving it

1. Read the two off-diagonal counts before the kappa. With 20 items, one changed
   decision moves plain agreement by 0.05 and kappa by more.
2. Open the disagreeing items. Look for the rubric criterion they share.
3. Tighten that criterion's anchors in `docs/RUBRIC.md` or the critic prompt, bump
   the prompt version, re-score the same outputs with the critic, and compare
   against the same human scores. For a number to report, score a fresh blind sheet.

No pass mark is set here. Which kappa is good enough is the owner's decision, and
commonly quoted bands are conventions, not measurements.

## Limits

- One person's scores are a reference, not ground truth. A second scorer on the
  same sheet would show how much two people disagree, which bounds what a critic
  can reach.
- 20 items is a small sample. Report the counts with the kappa, always.
- Scores near the threshold flip decisions on small differences. The mean absolute
  difference shows whether disagreements are near misses or real gaps.
