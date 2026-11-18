# Model-graded tier: DeepEval and promptfoo, only with a key

Status: **built and wired; never run against a model.** No judge score exists for
this pipeline or for any model. Everything below was checked without a key: the
DeepEval adapter against the real library with a fake judge model, the promptfoo
config with `promptfoo validate`. The first paid run is the owner's decision.

- Code: `measured_evals.judge` (pure), `measured_evals.deepeval_judge` (adapter)
- promptfoo: `packages/measured_evals/promptfoo/promptfooconfig.yaml`
- Pins: `packages/measured_evals/requirements-judge.txt`
- Tests: `tests/test_judge.py` (fake judge function), `tests/test_deepeval_judge.py`
  (real DeepEval, fake model; skipped unless DeepEval is installed)

## What this tier does

The golden set has 35 `judge` assertions: yes/no questions code can't answer, such
as "explains that the model still scores each criterion". The deterministic tier
leaves them pending. This tier asks a judge model each one and gets a score from 0
to 1; 0.7 or more counts as met.

## The fences, and why each exists

| Fence | What happens | Why |
|---|---|---|
| Key | No `ANTHROPIC_API_KEY`: prints `SKIPPED`, exits 0, reports no score | A skipped tier must never look like a passed one |
| Real outputs only | `--outputs DIR` is required; there is no fake writer here | Judging a fixture pays a model to grade nothing |
| Survivors only | An output that fails the deterministic tier is listed and not judged | It is already wrong; grading its tone is wasted spend |
| Approved items by default | Draft items need `--include-needs-eval`, and the result says it is not a quality claim | All 20 items are approved (2026-11-17), so the default run grades all 35 assertions when a key is present |
| Call budget | One call per assertion; over `--max-calls` (default 60) it stops before the first call | The cost is known before any money moves |
| Explicit judge model | `--model` or `MEASURED_EVALS_JUDGE_MODEL`; no default | A judge that also wrote the text tends to like it; choosing is deliberate |
| Scores are `needs_eval` | Printed and saved, never enforced | A judge varies between runs and has not been calibrated against a person yet |

## Run it

```bash
# what would be judged; no key, no call
uv run python -m measured_evals.judge run --outputs DIR --include-needs-eval --dry-run

# the real thing: costs one call per assertion
uv run --no-project --with ./packages/measured_evals \
    --with-requirements packages/measured_evals/requirements-judge.txt \
    python -m measured_evals.judge run --outputs DIR --model <judge-model> \
    --save-results outputs/judge_results.json
```

`DIR` holds one `<item-id>.md` per golden item. The printed report has scores only.
The judge's reasons can quote the output, so they go only to `--save-results`; keep
that file under `outputs/` (git-ignored).

The second grader, on the same outputs:

```bash
uv run python -m measured_evals.judge promptfoo-tests --outputs DIR \
    --out packages/measured_evals/promptfoo/tests.generated.json
cd packages/measured_evals/promptfoo
PROMPTFOO_GRADER=anthropic:messages:<judge-model> \
    npx --yes promptfoo@0.125.1 eval -c promptfooconfig.yaml --no-cache
```

## Why two tools

| | DeepEval (GEval) | promptfoo (`llm-rubric`) |
|---|---|---|
| Lives in | Python, inside the eval package | A YAML config, run with `npx` |
| Used for | The scores of record: `judge_mean` for a future baseline | A second opinion and a side-by-side viewer |
| Asked | `evaluation_steps(case)` | The same sentences, as the rubric |

Both are asked the same question, built by one function. When they disagree on a
case, the disagreement is about the grader, and that case is the first one to read
by hand. Neither replaces calibration: see [`CALIBRATION.md`](CALIBRATION.md) if it
is merged, and treat any judge as unproven until a person has scored the same outputs.

DeepEval is **not** a dependency of `measured-evals`. The package still declares
none; the judge requirements install only where this tier runs.

## What was checked, and against what

Pinned: `deepeval==4.3.4` (and every transitive dependency, in
`requirements-judge.txt`), `anthropic==2.10.0`, `promptfoo@0.125.1` (needs Node
22.22 or later). A test fails if the pins in the code, the requirements file, the
config and this page stop agreeing.

Checked against the installed library, which differs from some published examples:

- The GEval parameter enum is `SingleTurnParams` in 4.3.4. The
  [GEval docs page](https://docs.confident-ai.com/docs/metrics-llm-evals) still
  shows `LLMTestCaseParams`, which 4.3.4 does not export.
- With explicit `evaluation_steps`, GEval makes one model call per case. With only
  `criteria` it makes an extra call to write the steps. This tier always passes steps.
- GEval asks the judge for an integer from 0 to 10 and returns it divided by 10.
- DeepEval's `AnthropicModel` sends no temperature unless one is configured.
- promptfoo: the [`echo` provider](https://www.promptfoo.dev/docs/providers/echo/)
  returns the prompt unchanged at zero cost, which is how saved outputs are graded;
  [`llm-rubric`](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric)
  takes a `threshold` and a grader set in `defaultTest.options.provider`; Anthropic
  grader ids are
  [`anthropic:messages:<model>`](https://www.promptfoo.dev/docs/providers/anthropic/).
- promptfoo renders assertion values as templates. The generated tests keep all
  fixture and output text in `vars`, and the assertion value is a fixed template, so
  template syntax inside an output stays data. A keyless run with the `echo`
  provider confirmed that var values are not rendered a second time.
- `promptfoo validate config` accepts the config with the 35 generated tests.

Not checked, because it needs a paid call: a real judge's answers, DeepEval's
`AnthropicModel` end to end, and promptfoo's grader call. The first real run should
be a single item with `--max-calls` set low.

Telemetry is switched off where this tier runs: the adapter sets
`DEEPEVAL_TELEMETRY_OPT_OUT=YES` unless you set it, and the commands in CI should set
`PROMPTFOO_DISABLE_TELEMETRY=1`.

## Injection

The outputs and sources are untrusted text, and three golden items carry bait on
purpose. The first evaluation step tells the judge to treat them as data and ignore
instructions inside them. That is a net, not a guarantee: a judge can still be
talked into a high score. It is one more reason the scores are `needs_eval`.

## CI: applied

`.github/workflows/evals.yml` has two jobs for this tier (added with the owner's
approval, 2026-11-18):

1. **judge wiring** (every push and PR, no key): install `measured_evals` on its own
   with `requirements-judge.txt`, run `tests/test_deepeval_judge.py` and
   `tests/test_judge.py`, generate the promptfoo tests from the fake writer and run
   `npx --yes promptfoo@0.125.1 validate config`. It calls no model.
2. **model-graded** (manual start only, with an `outputs_dir` input): run
   `python -m measured_evals.judge run` and, when a judge model is named, the
   promptfoo eval. Each step is guarded by `if: env.ANTHROPIC_API_KEY != ''`, with
   the key mapped from a repository secret. Without the secret it prints SKIPPED and
   stays green. Never on `push` or `pull_request`: spending is a person's decision,
   and a manual start is that decision.

The model-graded job has not run with a key. No judge score exists.

## Making a judge score real (owner)

1. Approve golden items ([`GOLDEN_SET.md`](GOLDEN_SET.md)). Done for all 20.
2. Run the real pipeline on them and save each output as `<item-id>.md`.
3. Calibrate the judge against your own scores on those outputs.
4. Run this tier, read the saved reasons, and only then record `judge_mean` in a
   baseline with `status: real`.
