# Launch checklist: Proofline goes public (GROW-16, GROW-17)

Ordered steps, no dates. Do them in order: each one assumes the ones above it are
done. The repository is already public, so "launch" here means the launch materials
and a repository that is ready to be read by strangers.

Marked **(Hassan)**: only you can do it, because it needs your accounts, your keys,
your repository settings or your voice. Everything else is already done on the branch
`docs/launch-pack`, or is a code task for a later step.

Launch "done when" (GROW-17): the repository is ready, `measured-evals` is published,
the case study is live on hassanmansour.com, the demo video is posted, and build
post #3 is on LinkedIn and pinned to Featured.

---

## A. Review and merge the launch pack

1. **(Hassan)** Read [`CASE_STUDY.md`](CASE_STUDY.md) start to end. Every number in it
   is pinned to commit `8fdc21c` and a source. If C5 has merged since, re-run the
   commands at the bottom of the case study and update section 3.1 with the new
   commit and counts.
2. **(Hassan)** Read [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) and
   [`BUILD_POST_3.md`](BUILD_POST_3.md). Change the narration and posts until they
   sound like you.
3. **(Hassan)** Read the new `SECURITY.md` and `CONTRIBUTING.md`. Two choices were made
   for you there, and you can reverse either: issues are welcome, with an issue first
   for anything beyond a small fix; security reports go through GitHub's private
   reporting (step B4 turns it on).
4. **(Hassan)** Open the pull request for `docs/launch-pack` and merge it once CI is
   green. Agents don't merge to `main`.

## B. Repository polish

1. **README status table.** Corrected on this branch to match the code at `8fdc21c`:
   the inbox's script and its three decision buttons, CI triggers, the Evals
   workflow's two keyless jobs, the licence row and `scripts/` in the layout. When C5
   merges, re-check three places in the README: the status table (a row for MCP
   tools), the "Not built yet" paragraph, and the roadmap's `◀ now` marker.
2. **Portal evidence lines.** `measured-portal/content/proofline.yaml` quotes README
   lines verbatim at a pinned commit. After any README merge, set `source.commit` to
   the new `main` commit and run `npm run check:proofline -- /path/to/ProofLine` in
   the portal repo. The README changes on this branch keep every quoted line intact.
3. **Link the case study from the README** (after A4): one line under "Status: what
   works today", for example
   `Case study: [what is built, measured and not measured](docs/CASE_STUDY.md).`
4. **(Hassan) Turn on private vulnerability reporting.** Settings → Security
   (Advisories) → Private vulnerability reporting → Enable. It is off today, and
   `SECURITY.md` points to it.
5. **(Hassan) Description, website and topics.** They are empty today. Settings →
   General, or the gear next to "About" on the repo page:
   - Description: `Eval-gated, multi-agent content pipeline built with LangGraph. Nothing ships without passing the evals and a human sign-off.`
   - Website: the case study page on hassanmansour.com, once step E1 has put it there.
   - Topics: `langgraph`, `langchain`, `llm`, `ai-agents`, `multi-agent-systems`,
     `human-in-the-loop`, `llm-evaluation`, `evals`, `prompt-injection`, `python`,
     `fastapi`, `anthropic`

   Or from a terminal signed in as you:
   ```bash
   gh repo edit hassanmansour-dev/ProofLine \
     --description "Eval-gated, multi-agent content pipeline built with LangGraph. Nothing ships without passing the evals and a human sign-off." \
     --add-topic langgraph --add-topic langchain --add-topic llm --add-topic ai-agents \
     --add-topic multi-agent-systems --add-topic human-in-the-loop --add-topic llm-evaluation \
     --add-topic evals --add-topic prompt-injection --add-topic python --add-topic fastapi \
     --add-topic anthropic
   ```
6. **(Hassan) Protect `main`.** It has no protection today. Settings → Rules →
   Rulesets → New branch ruleset, target `main`: require a pull request, require the
   status checks `secret + PII scan` and `lint · types · tests` (CI) and
   `deterministic tier (fake writer, no key)` and
   `judge wiring (fakes only, no key, no model call)` (Evals), block force pushes,
   block deletion. Leave the manual `model-graded tier` job out: it doesn't run on
   pull requests.
7. **(Hassan) Optional clean-up.** Wiki and Projects are on and unused (docs live in
   the repo, planning in Jira): Settings → General → Features. Decide on Discussions.
8. **Badges.** The CI badge is already there. Two more are worth adding next to it
   (paste under the title, after A4):
   ```markdown
   [![Evals](https://github.com/hassanmansour-dev/ProofLine/actions/workflows/evals.yml/badge.svg)](https://github.com/hassanmansour-dev/ProofLine/actions/workflows/evals.yml)
   [![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
   ```
9. **(Hassan) Re-run the pre-public check** (PLAN L2, ADR-0013), on a fresh clone of
   `main`:
   ```bash
   gitleaks git --redact --no-banner .        # v8.30.1, the version CI pins
   git ls-files -z -- . ':!uv.lock' ':!scripts/test_pii_scan.py' | xargs -0 python3 scripts/pii_scan.py
   git log -p --all | grep -i -n -E '<employer and client names, internal terms>'
   ```
   The third line is yours to fill in. The terms are deliberately not written in this
   public file. Expect no matches.

## C. The demo video (GROW-16)

1. **(Hassan)** Set up `.env.demo.local` and the demo database as
   [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) describes ("Before you record").
2. **(Hassan)** Record the full version (about 3½ minutes) and the 60-second cut.
3. **(Hassan)** Check every frame: no `.env`, no key, no tailnet name, no topic from
   your private inbox.
4. **(Hassan)** Upload it, then add the link to the README (under the title) and to
   the case study (at the top).

## D. Measurement: the first real numbers

The launch can go out without these: the case study and the post say plainly that
quality is not yet measured. They are what turns "not yet measured" into a number,
and GROW-14 (which PLAN's L2 waits on) needs them.

1. **Prerequisite, a code task: a golden-set runner.** Nothing in the repo yet runs
   the pipeline on a golden item's own fixture sources and saves the result as
   `<item-id>.md`. `proofline run` uses stub notes or web search, so its citations
   wouldn't refer to the item's sources, and the deterministic tier would be checking
   the wrong thing. It needs a researcher that serves an item's fixture sources and a
   command that writes one file per approved item, in the format
   `measured_evals.suites` reads ([`EVALS.md`](EVALS.md)), with tests. It belongs
   after C5, as its own change and Jira ticket.
2. **(Hassan) Live smoke test, possible today** (costs money: 5 live tests, real
   model, plus web search for 3 of them):
   ```bash
   uv run pytest -m live -s
   ```
3. **(Hassan) The golden-set run, with your keys**, once the runner from D1 exists.
   Save the outputs in a directory under `outputs/` (git-ignored), called `DIR` below.
4. **Deterministic tier on the real outputs and a draft baseline** (no key needed):
   ```bash
   uv run python -m measured_evals run --approved-only --outputs DIR \
       --write-draft-baseline evals/baselines/content_v0.json
   ```
   **(Hassan)** Read the result. If you accept it, follow "Making it real" in
   [`EVALS.md`](EVALS.md): set `status` to `real`, fill `source` and `approved_by`,
   and point the Evals workflow at the file.
5. **(Hassan) Critic calibration, blind**, on the same outputs
   ([`CALIBRATION.md`](CALIBRATION.md)):
   ```bash
   uv run python -m measured_evals calibration-sheet \
       --outputs DIR --critic-scores critic.json --out-dir outputs/calibration
   # score calibration_sheet.csv yourself, without looking at the critic's scores
   uv run python -m measured_evals calibration-agreement \
       --sheet outputs/calibration/calibration_sheet.csv --critic-scores critic.json --threshold 8
   ```
   `critic.json` holds the critic's score for each output. The judge log has them per
   action id. The runner from D1 should write this file too.
6. **(Hassan) The model-graded tier, one item first.** The judge needs an output for
   every item in the golden file it reads, so the first paid run uses a one-item copy
   of the set. Pick a judge model that didn't write the outputs: the writer and critic
   default to `claude-sonnet-5-5`.
   ```bash
   mkdir -p outputs
   awk '/^\[\[items\]\]$/{n++} n<2' packages/measured_evals/src/measured_evals/data/golden/content_v0.toml \
       > outputs/golden_one.toml                     # header + the first item only
   uv run python -m measured_evals.judge run --golden outputs/golden_one.toml --outputs DIR --dry-run
   set -a; . ./.env; set +a                          # the judge reads ANTHROPIC_API_KEY from the shell
   uv run --no-project --with ./packages/measured_evals \
       --with-requirements packages/measured_evals/requirements-judge.txt \
       python -m measured_evals.judge run --golden outputs/golden_one.toml --outputs DIR \
       --model <judge-model> --max-calls 5 --save-results outputs/judge_one.json
   ```
   The dry run prints how many assertions it would grade, one call each (2 for the
   first item). Then the full set: the same command without `--golden`, with
   `--max-calls 60` (the default) and `--save-results outputs/judge_results.json`.
7. Only after D4–D6 produce numbers with a source does the case study get a measured
   results section, and the site and portal a score.

## E. Publish the case study

1. **(Hassan) hassanmansour.com** (repo `hassanmansour-dev/hassanmansour-com`). The
   Proofline project card (`index.html#proofline`, and `fr/index.html`) links only to
   the repository today. Add:
   - a case study page (English, and French if the project pages stay bilingual) made
     from `docs/CASE_STUDY.md`, keeping the commit pin, the "not yet measured" table
     and the repo link;
   - on the card: links to the case study page and the demo video. The card's line
     "no score is published: the model-graded evaluations have not been run yet"
     stays true until D6.
2. **(Hassan) The Measured portal** (measured.sanovinci.com, repo
   `hassanmansour-dev/measured-portal`). The Build log page is
   `content/proofline.yaml`. Bump `source.commit` after the merge and run the check
   (B2). Add the case study and the video as links. The `log` block says there are no
   entries yet, so the launch could be the first one.
3. **(Hassan) The SanoVinci page** (sanovinci.com; source
   `sites/sanovinci/content.yaml` in `measured-curriculum`). Proofline appears in the
   Products text without a link, and `proofline` in the links block points to the
   repository. Link "Proofline" in the Products text to the repository or the case
   study page, in English and French. Rebuild with the site's own build and tests.
4. **(Hassan)** Set the repository's Website field (B5) to the case study page.

## F. Release

1. **(Hassan) Publish `measured-evals` to PyPI** (GROW-14, part of L2). The repo has
   no release workflow and no PyPI project yet. The package is at version `0.0.1`
   with no runtime dependencies. Decide the first public version, create the PyPI
   project under your account (trusted publishing from GitHub Actions is the safer
   route), then build and publish:
   ```bash
   uv build --package measured-evals
   uv publish                      # or from a workflow with trusted publishing
   ```
2. **(Hassan) Tag `v1.0.0`** on `main` once B to F1 are done (PLAN L2), with a
   GitHub release that links to the case study.

## G. Announce

1. **(Hassan)** Post build post #3 from your own account ([`BUILD_POST_3.md`](BUILD_POST_3.md),
   English and French). Put the repo, case study and video links in the first comment.
   Re-check its numbers first; the commands are listed under the drafts.
2. **(Hassan)** Pin it to Featured on your profile (GROW-17).
3. **(Hassan)** Move GROW-16 to Done (case study reviewed, video recorded) and GROW-17
   to Done (all four live). GROW-17 is the gate that opens Phase 2.
