# ADR-0017: Approval inbox v0: location, sign-in, network, and edit deferred

- Status: proposed (2026-11-14, built in C4; Hassan chose the options below on 2026-11-14)
- Date: 2026-11-14
- Phase/Step: Phase 1 / C4 (GROW-83)

## Context
C2 (ADR-0016) pauses a run at the supervisor's gate with an interrupt whose payload is
`action_id, task_id, type, target, preview, risk_level` and whose `response_schema` is
an `ApprovalDecision` for that action. C4 adds the human side: a mobile web page that
lists the waiting actions and approves or rejects them, resuming the thread from its
Postgres (or SQLite) checkpoint. GROW-83 also lists "edit".

Open questions, decided by Hassan on 2026-11-14:
1. Where the inbox code lives. ADR-0014 left it to C4.
2. Edit: approving an edited preview needs a contract change.
3. Sign-in: the page is reachable from a phone, so it needs sign-in from day one.
4. Where it runs, without a public URL that has no auth.

Found while planning: the checkpointer lists every checkpoint across threads
(`alist(None)`, verified on SQLite and Postgres), so the inbox can find paused runs
without an index of its own. And resuming by interrupt id
(`Command(resume={interrupt_id: value})`) works on both.

## Decision

**1. Location: `measured_ops.inbox`, behind an optional `inbox` extra.** Approval
belongs to the supervisor (ADR-0014). `service.py` has no web code (list, decide);
`web.py` is the FastAPI app; `__main__.py` has `hash`, `seed` and `serve`.
`proofline/api.py` stays `/health` only. Agents and the policy are passed to
`create_inbox_app(settings, agents, policy)`, never imported, so the private repo
serves the same inbox with its real agents.

New runtime dependencies, all in the `inbox` extra (and the dev group): `fastapi`,
`uvicorn`, `jinja2` (autoescaped templates), `python-multipart` (form posts),
`itsdangerous` (Starlette's signed session cookie). The base `measured-ops` package
is unchanged.

**2. Listing and deciding (`Inbox`).**
- A run is waiting when its latest checkpoint is at `gate` with an interrupt. Threads
  come from `checkpointer.alist(None)`. This reads every checkpoint: fine at v0
  volume, revisit in C10.
- `decide` resumes with `Command(resume={interrupt_id: decision})` and **never**
  `ainvoke(None)` (ADR-0016: after a refused answer the refused value is still the
  pending resume). A refused answer (`ValidationError` or `ActionBlockedError` from
  the gate, or an invalid `ApprovalDecision`) raises `Refused`; the run stays paused
  and the next answer is a new `Command`.
- **The interrupt id is the double-tap guard**: an answer for an interrupt that is no
  longer pending is `AlreadyDecided` (HTTP 409), including when the run has paused
  again on its next action. A per-thread `asyncio.Lock` only serialises two taps
  arriving at once.
- **A paused action the policy has since tightened to "never" is shown, not hidden**,
  marked "blocked by policy", with only Reject offered. Hiding it would leave the run
  paused forever. The policy logs the warning; the gate refuses an approval
  (`check_decision`); rejecting ends the run (tested).
- The form is built for the `ApprovalDecision` answer and reads the feedback limit
  from the interrupt's `response_schema`. A fully schema-driven form waits until a
  second answer type exists.

**3. Sign-in: one user, one passphrase.**
- The passphrase is stored only as a `hashlib.scrypt` hash
  (`scrypt$n$r$p$salt$key`, n = 2^17, r = 8, p = 1: OWASP's minimum), made with
  `python -m measured_ops.inbox hash`. A hash whose parameters can't run is refused
  at startup.
- The hash and the session secret (at least 32 characters) come from the
  environment (`MEASURED_OPS_INBOX_PASSPHRASE_HASH`, `MEASURED_OPS_INBOX_SESSION_SECRET`).
  **No defaults: the app refuses to start without them.**
- 5 failed sign-ins in 15 minutes lock sign-in for 15 minutes (global: one user,
  and behind `tailscale serve` every request comes from 127.0.0.1). **Sign-in attempts
  run one at a time** (an `asyncio.Lock` around the throttle check, the awaited scrypt
  verify and recording the failure): otherwise parallel guesses are all checked before
  any failure counts (found in review, tested).
- Session: Starlette's signed cookie `__Host-mo_inbox`, `HttpOnly`, `Secure`,
  `SameSite=Strict`, carrying a random session id. The server keeps live session ids
  and their sign-in time in memory: the session ends **12 hours after sign-in**
  whatever the activity (Starlette's own `max_age` slides), **sign-out revokes it**
  (a copied cookie stops working), and a restart signs everyone out. Rotating the
  session secret is the other kill switch. A new session is issued on sign-in.
- Form posts are size-limited before anything else is parsed: no file parts, at most
  4 fields on sign-in and 6 on a decision.
- Every POST carries a CSRF token. Every response has a strict CSP
  (`default-src 'none'`, no inline anything), `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. No JavaScript.
  Templates autoescape; previews (agent output, possibly fetched web text) render as
  escaped text only.

**4. Network: local, on 127.0.0.1, reached over `tailscale serve`, never `funnel`.**
`serve` binds 127.0.0.1 only (no flag to change it). The phone reaches it at the
machine's tailnet HTTPS name through `tailscale serve --bg --https=443
http://127.0.0.1:8765`, which is tailnet-only; `tailscale funnel` would make it public
and is never used. Two locks: the device must be on Hassan's tailnet, and the person
must know the passphrase. Steps in `docs/INBOX.md`.

**4b. Postgres through a pool.** `open_checkpointer` now opens a small
`psycopg_pool.AsyncConnectionPool` (with a connection check) instead of one connection,
so the inbox, the first long-running process, recovers from a database restart
(verified by restarting the container). The ops checkpoints should have their own
database; a thread the inbox can't read is logged and skipped, not fatal.

**5. One process, enforced.** The decision lock is in memory, so `serve` runs one
uvicorn worker, and the app's lifespan takes an exclusive `flock` on
`MEASURED_OPS_INBOX_LOCK_PATH`: a second process (another `serve`, or
`uvicorn --workers 2`) refuses to start.

**6. Edit is deferred to C8.** C4's done-when becomes approve and reject. Approving
edited content would need an `ApprovalDecision.edited_preview` field, the edited text
would skip the eval/guard node (C7) unless re-checked, and what a preview becomes when
an action runs isn't defined until C5 (tools) and C8 (content agent). Until then,
reject with feedback covers it, and C8 turns feedback into a revision `Task`.

## Alternatives considered
- **Inbox in `proofline/api.py`.** The supervisor's approval would live in one agent's
  package (ADR-0014 rejected this ownership).
- **An inbox index table written when a run pauses.** The gate can't write it (no side
  effects before `interrupt()`), so every caller that starts runs would have to.
  The checkpointer already knows; revisit if listing gets slow (C10).
- **Edit now, with an ADR'd `edited_preview`.** About half a day, but it fixes the
  contract before C5/C7/C8 define what runs and how it's checked.
- **Tailscale identity headers as the only auth.** No auth code, but safety rests on
  bind and proxy configuration, and tests need a bypass. The docs we checked don't
  describe the headers.
- **Passkeys (WebAuthn).** Best long-term; more code and a dependency. Hardening (GROW-15).
- **GitHub OAuth.** Needs a reachable callback and more moving parts.
- **Cloudflare Tunnel + Access, or deploying.** A public hostname behind SSO, or C10's
  hosted setup; both more setup than C4 needs. Deploying is an approval-policy action.
- **Hide actions the policy tightened to "never".** Leaves the run paused forever.

## Consequences
- Good: one inbox for every agent; nothing new to keep in sync with the checkpointer;
  refusals and double taps leave the run answerable; no public surface.
- Bad / cost: five new dependencies (optional extra); listing scans all checkpoints;
  a single process, and a restart signs you out; passphrase sign-in is phishable
  compared to passkeys; reaching the inbox needs the laptop on and on the tailnet.
  No `TrustedHostMiddleware` yet: DNS rebinding is already stopped by the `Secure`
  cookie and CSRF, and allowing the tailnet name would need one more setting.
- Revisit when: listing is slow (index, C10); the inbox runs on hosted infra (C10:
  auth must not depend on Tailscale then); a second answer type exists (schema-driven
  form); edit lands (C8); hardening (GROW-15: passkeys, audit log of sign-ins).
