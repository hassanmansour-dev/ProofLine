# ADR-0020: Ready to paste, posted marks, explicit reject, and the inbox's first script

- Status: proposed (2026-11-16, C8, in PR #13 at Hassan's request; amends ADR-0017's
  "no JavaScript")
- Date: 2026-11-16
- Phase/Step: Phase 1 / C8 (GROW-87)

## Context
Reviewing #13, Hassan found two gaps. An approved `approve_draft` leaves the inbox and
is saved nowhere he can copy from, so the text he must paste is out of reach. And one
Reject button that revises when the box has text and stops when it's empty is too easy
to get wrong on a phone. ADR-0017 made the inbox server-rendered with no JavaScript and
a CSP of `default-src 'none'`; copying to the clipboard needs a script.

## Decision

**1. "Ready to paste" page** (`GET /ready`). Approved drafts (`ready` actions of the
paste types, `approve_draft` by default), newest approval first, read from the
checkpointer like the rest of the inbox. Each preview is split on its `## ` headings into
blocks (LinkedIn post, clip script, lesson outline), each in a read-only text box with
a **Copy** button. Same sign-in, CSRF and autoescaping as every other page.

**2. "Mark as posted"** (`POST /ready/{action_id}/posted`, CSRF) only **records** that
Hassan posted it, and hides it from the list. It calls nothing outside the system:
there is no executor for `approve_draft` (ADR-0019), and this route never resumes a run.
The record is a line in a local JSONL file, `{"action_id", "posted_at"}`
(`MEASURED_OPS_POSTED_PATH`, default `.data/posted.jsonl`, gitignored), not a checkpoint
write: updating a finished run's state would re-run the graph's routing, which could
start a revision. Marking twice is harmless.

**3. `proofline ready`** prints the approved, not-yet-posted drafts as Markdown, newest
first; `--out FILE` writes them to a file that must be under `outputs/` (gitignored).

**4. Explicit reject.** The decision form has three buttons:
- **Approve**;
- **Revise with this feedback**: needs text (refused by the server when empty; the
  script also disables the button while the box is empty) and a revision left;
- **Reject and stop**: ends the run and **ignores any text in the box**, so it can never
  queue a revision.
The page shows where the run is: "First draft · up to 3 revisions" or "Revision 2 of 3"
(the task's `revision`, ADR-0019). With no revisions left, Revise is refused.

**5. The inbox's first script** (amends ADR-0017). One first-party file,
`/static/inbox.js`, served by the inbox; the CSP adds `script-src 'self'` and nothing
else (no inline scripts, no external origins). It only copies a block to the clipboard
(falling back to selecting the text when the clipboard isn't available) and disables
Revise while the feedback box is empty. Every page still works without it: blocks are
selectable text, and the server enforces the feedback rule.

## Alternatives considered
- **Posted flag in the run's state** (`aupdate_state`) — keeps everything in one store,
  but writing to a finished thread re-runs routing (a rejection with feedback in the
  same round could revise); a separate append-only record can't touch a run.
- **Save approved drafts to files at approval time** — another copy of the text on
  disk, out of sync with the inbox; the checkpointer already holds it.
- **No JavaScript, selection only** — works, but copying three blocks on a phone by
  long-press is the friction Hassan asked to remove.
- **One Reject button whose meaning depends on the box** — the current design; the
  error it invites (a stray word revising, or a lost revision) is what this fixes.

## Consequences
- Good: an approved draft is a tap away from the clipboard; rejecting says what it does.
- Bad / cost: the inbox ships a script (small, first-party, CSP-pinned to self); posted
  marks live beside the checkpointer, not in it (a moved database loses them, harmlessly:
  drafts reappear as not posted).
- Revisit when: C5 builds executors (still none for `approve_draft`), C9 adds edit, or
  posted marks need to travel with the database (move them to a table).
