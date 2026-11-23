# Demo video script: Proofline (3–4 minutes, plus a 60-second cut)

For Hassan to record himself (GROW-16). It uses the public demo config only: the demo
policy, the demo price table, and a separate demo database, so no real run, judge-log
line or private setting appears on screen.

Two versions of the middle section:

- **With keys** (recommended): a real draft from the content agent, reviewed on the
  phone. Needs `ANTHROPIC_API_KEY` in `.env` (and `TAVILY_API_KEY` if you use web
  research). It costs one content-agent run, plus one more per revision you request.
- **Offline**: the demo echo agent. No key, no model call. Use it if you'd rather not
  spend, or as a backup take.

Every command below was checked on 2026-11-23 at commit `8fdc21c`, except the ones
that need a key (`proofline inbox`, `proofline propose`, `proofline ready`). The
offline inbox flow (seed, sign in, open, approve, revise) was checked locally over
HTTP.

## Before you record

### 1. A demo environment file (once)

Put the demo settings in **`.env.demo.local`** at the repo root. That name is
git-ignored (`.env.*.local`); `.env.demo` would **not** be. Contents:

```bash
MEASURED_OPS_INBOX_CHECKPOINTER_URL=sqlite:///.data/demo_ops.db
MEASURED_OPS_JUDGE_LOG_PATH=.data/demo_judge_log.jsonl
MEASURED_OPS_POSTED_PATH=.data/demo_posted.jsonl
MEASURED_OPS_INBOX_LOCK_PATH=.data/demo_inbox.lock
LANGSMITH_TRACING=false
PROOFLINE_BRAND_NAME="Build Clearly"
MEASURED_OPS_INBOX_PASSPHRASE_HASH=<from: uv run python -m measured_ops.inbox hash>
MEASURED_OPS_INBOX_SESSION_SECRET=<from: python3 -c "import secrets; print(secrets.token_urlsafe(48))">
```

Why each line:

- The demo database and the two demo log files keep recording decisions out of your
  real judge log, which GROW-13 reads. Demo approvals must not count as agreement data.
- `PROOFLINE_BRAND_NAME="Build Clearly"` is the public default. Remove the line if you
  want your own brand name on screen.
- Use a **demo passphrase** for the recording, not your real one. A demo hash and
  session secret mean nothing about your real inbox leaks if a frame shows them.

Load it in **every terminal you record in**, before any other command:

```bash
set -a; . ./.env.demo.local; set +a
```

`python -m measured_ops.inbox` doesn't read `.env` files, so these have to be in the
shell. `proofline inbox` and `proofline propose` load `.env` for your keys, and the
values already in the shell win (python-dotenv doesn't override them), so the demo
database is used.

### 2. Screen hygiene

- Never `cat .env` or open it in an editor while recording. Keys only live there.
- Clear the terminal and its scrollback before each take (`clear && printf '\e[3J'`).
- Use a short prompt (`export PS1='$ '`): no username, hostname or home path.
- On the phone, the address bar shows your tailnet name (`<machine>.<tailnet>.ts.net`).
  Crop it out, or blur it in the edit.
- Turn on Do Not Disturb on the phone and the laptop.
- Terminal font at 18–20 pt. Record the phone with its own screen recorder, then cut
  the two sources together.

### 3. Get the inbox running (off camera)

Terminal A, with keys:

```bash
set -a; . ./.env.demo.local; set +a
uv run proofline inbox                 # 127.0.0.1:8765, content agent + demo echo agent
```

Terminal A, offline:

```bash
set -a; . ./.env.demo.local; set +a
uv run python -m measured_ops.inbox seed --count 3   # three demo runs paused at the gate
uv run python -m measured_ops.inbox serve
```

Then, once (it stays on): `tailscale serve --bg --https=443 http://127.0.0.1:8765`
([`INBOX.md`](INBOX.md), section 3). **Never `tailscale funnel`.** Sign in on the phone
with the demo passphrase, so the take doesn't start on the sign-in page.

With keys, put one draft in **before** recording too. A content-agent run takes
minutes, and the take only needs its result. Terminal B:

```bash
set -a; . ./.env.demo.local; set +a
uv run proofline propose --topic "Why every AI loop needs an exit" --audience "developers new to AI"
```

It prints `waiting in the inbox: <run id>` when the draft reaches the gate. You will
run the same command again on camera (scene 3) for a second draft; the first one is the
one you review.

## The full version: about 3½ minutes

Each scene: what's on screen, then the narration. Narration lines are written to be
read as they are. Don't add numbers that aren't in [`CASE_STUDY.md`](CASE_STUDY.md).

### Scene 1 · Cold open (0:00–0:15) · phone

**Screen:** the inbox list on the phone, "Waiting for you" with one draft.

> This is my approval inbox. An agent wrote a draft, the checks ran, and now it waits
> for me. Nothing it writes goes public until I say so. Let me show you how it's built.

### Scene 2 · What it is (0:15–0:45) · browser, then terminal

**Screen:** the README on GitHub, scrolled to the status table, then the pipeline
mermaid diagram. Then terminal:

```bash
uv run proofline mermaid
```

> Proofline is an open-source agent system built with LangGraph. The first agent turns
> a topic into a short technical script: plan, research, write, then a critic scores
> it and sends it back until it passes or runs out of revisions. Two exits, both
> decided in code, not by the model.

### Scene 3 · Propose a topic (0:45–1:10) · terminal B

**With keys:**

```bash
uv run proofline propose --topic "When not to use an agent" --audience "developers new to AI"
```

Let it start, then cut. Don't wait for it on camera.

> Topics go in by hand for now. The agent plans, researches, writes and critiques,
> and then proposes one action: approve this draft. It can't publish. That action
> type has no executor at all.

**Offline:** skip the command. Show `packages/measured_ops/src/measured_ops/config/approval_policy.demo.toml`
in the editor instead, and say the policy line from scene 5 here.

### Scene 4 · Review on the phone (1:10–2:20) · phone

**Screen, in order:**

1. Tap the waiting draft. Scroll slowly through the three sections: **LinkedIn post**
   (with its sources), **Clip script**, **Lesson outline**.
2. Stop on **Checks**: quality, injection, pii, target_injection, target_pii, each
   passed or failed with a reason.
3. Stop on **Cost of this run**: tokens and dollars per agent. Offline, it says "No
   model calls."
4. Point at "First draft · up to 3 revisions".
5. Type feedback, for example "Shorter hook, one concrete example", and tap **Revise
   with this feedback**. The page says it is revising.
6. Cut. (With keys this takes minutes. Offline the echo agent comes back at once.)
7. Open the revised draft: it now says "Revision 1 of 3". Tap **Approve**.
8. With keys: open **Ready to paste**, tap **Copy** on the LinkedIn post block, then
   **Mark as posted**. Offline: skip this step, because the echo agent's actions are
   `publish_public`, not drafts to paste, so Ready to paste stays empty.

> Each draft comes with its checks. These run in code, with no model: structure and
> word limit, prompt-injection patterns, and personal data. A failed check can only
> add a human approval. It can never remove one. The reasons never quote what they
> matched, so personal data doesn't end up in logs or traces.
>
> This is what the run cost, per agent. If a model isn't in the price table, it
> shows "unknown", never zero.
>
> I don't like the hook, so I send it back with feedback. The revision runs in the same
> thread, at most three times. Approving doesn't post anything: the draft moves to Ready
> to paste, I copy it, and I post it myself. "Mark as posted" only records that I did.

### Scene 5 · Fail closed (2:20–2:50) · terminal

```bash
uv run pytest packages/measured_ops/tests/test_ops_e2e.py -k failed_check -v
```

Expected: the SQLite case `PASSED`. The Postgres case says `SKIPPED` unless you set
`MEASURED_OPS_TEST_DATABASE_URL` to a throwaway database on your local Postgres. You
can blur or crop that line, or set the variable for this take.

> This test pauses a run in one process and resumes it in another, from the checkpoint.
> The action would normally run on its own, but a check failed, so it waits for a human,
> across the restart. Unknown action types wait too. And some actions are never allowed:
> no approval unblocks them.

### Scene 6 · What is measured, and what isn't (2:50–3:20) · terminal

```bash
uv run python -m measured_evals run
uv run python -m measured_evals run --writer fake-careless
```

Expected last lines: `deterministic tier: 20/20 items passed · 35 judge assertion(s)
not run (model-graded tier)`, then `deterministic tier: 0/20 items passed · ...`.

> Twenty approved golden topics, checked in CI on every pull request. But look at what
> wrote these: a fake writer that passes by construction, and a careless one that must
> fail. That proves the checks work. It doesn't measure the pipeline. No real output
> has been scored against this set yet, so I'm not showing you a quality score. That's
> the next step.

### Scene 7 · Close (3:20–3:35) · browser

**Screen:** the case study (`docs/CASE_STUDY.md`) on GitHub, scrolled to "What is not
built yet", then the repo home.

> Tools, scheduling and more agents come next, one at a time. The code, the decision
> records and the case study are on GitHub, Apache 2.0. Link below.

## The 60-second cut

Same recordings, cut down. No new commands.

| Time | Source | Narration |
|---|---|---|
| 0:00–0:08 | Scene 1, phone list | "My agent wrote a draft. Nothing it writes goes public until I approve it." |
| 0:08–0:20 | Scene 2, mermaid diagram | "Plan, research, write, and a critic that sends it back. Two exits, both decided in code." |
| 0:20–0:38 | Scene 4, steps 1–3: preview, checks, cost | "Every draft comes with checks that run without a model, and what the run cost. A failed check can only add a human approval, never remove one." |
| 0:38–0:48 | Scene 4, steps 5 and 8: Revise, then Copy | "I send it back with feedback, or approve it and paste it myself. It never posts on its own." |
| 0:48–0:56 | Scene 6, the 20/20 line | "The golden set is wired into CI. No real output has been scored yet, so there's no score to show." |
| 0:56–1:00 | Repo page | "Open source, built in public. Link below." |

Offline takes: use the echo agent's preview in the 0:20 shot and end the 0:38 shot on
**Revise**. Don't show Copy (Ready to paste is empty in the offline flow).

## After recording

- Watch the full take once just for the screen: no `.env`, no keys, no tailnet name,
  no real topics from your private inbox.
- The demo database lives at `.data/demo_ops.db` and is git-ignored. Keep it or remove
  it yourself. The repo's Claude Code hooks won't delete anything under `.data/`.
- Captions: the narration above is the transcript. Add French captions if the video
  goes on the French pages.
