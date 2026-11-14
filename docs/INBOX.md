# Approval inbox: run it and reach it from your phone

The inbox (C4, ADR-0017) lists the actions waiting at the supervisor's gate. You
approve or reject each one; the run resumes from its checkpoint. It runs on your own
machine, on `127.0.0.1` only, and your phone reaches it over **Tailscale Serve**
(tailnet only). **Never `tailscale funnel`**: that would put it on the public internet.

Everything in this repo is demo data. Real agents, the real policy and real secrets
live in `measured-ops-private` and your local env file.

## 1. Secrets (once)

```bash
uv sync                          # the dev group includes measured-ops[inbox,postgres]
# (outside the dev setup: uv sync --all-packages --extra inbox)
uv run python -m measured_ops.inbox hash
# type a passphrase of 12+ characters twice; it prints scrypt$131072$8$1$...
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # session secret
```

Put both in your local env file (never committed), plus where the runs are:

| Variable | Required | Default |
|---|---|---|
| `MEASURED_OPS_INBOX_PASSPHRASE_HASH` | yes, no default | |
| `MEASURED_OPS_INBOX_SESSION_SECRET` | yes, 32+ characters, no default | |
| `MEASURED_OPS_INBOX_CHECKPOINTER_URL` | no | `sqlite:///.data/ops_checkpoints.db` |
| `MEASURED_OPS_INBOX_PORT` | no | `8765` |
| `MEASURED_OPS_INBOX_LOCK_PATH` | no | `.data/inbox.lock` |
| `MEASURED_OPS_POLICY_PATH` | no | the bundled demo policy |

The inbox refuses to start if either secret is missing or unusable. To use your
local Postgres, set `MEASURED_OPS_INBOX_CHECKPOINTER_URL=postgresql://...` and give the
supervisor's checkpoints **their own database** (not the one the content pipeline
uses): the inbox lists every thread it finds, and skips, with an error in the log,
any it can't read. It reconnects on its own after a Postgres restart.

Rotating `MEASURED_OPS_INBOX_SESSION_SECRET` (and restarting) signs out every session.
Signing out already revokes that session on the server; restarting the inbox signs
everyone out.

## 2. Run it

```bash
uv run python -m measured_ops.inbox seed --count 3   # demo runs that pause at the gate
uv run python -m measured_ops.inbox serve            # 127.0.0.1:8765, one process
```

`serve` always binds `127.0.0.1` and runs one worker. Don't start it with
`uvicorn --workers N`: decisions are serialised in memory, and a second process
refuses to start (it can't take the lock file).

Check it from the machine itself: `curl -I http://127.0.0.1:8765/login` → `200`.

## 3. Reach it from your phone with Tailscale Serve

Run Tailscale in **one** place only. Tailscale's WSL guide warns that running it on
both the Windows host and inside WSL 2 breaks traffic, and recommends the Windows host.

**Option A: Tailscale on the Windows host (recommended by Tailscale).**
1. Install Tailscale for Windows and sign in.
2. Check that Windows reaches the inbox in WSL: in PowerShell,
   `curl.exe -I http://127.0.0.1:8765/login` → `200`. (WSL 2 forwards localhost to
   Windows by default. If this fails, check `localhostForwarding` in `.wslconfig`, or
   use option B.)
3. In PowerShell: `tailscale serve --bg --https=443 http://127.0.0.1:8765`

**Option B: Tailscale inside WSL 2 (only if Windows doesn't run it).**
1. Enable systemd in WSL if it isn't (`tailscaled` runs as a service): add
   `[boot]` / `systemd=true` to `/etc/wsl.conf`, then `wsl --shutdown` from PowerShell.
2. `curl -fsSL https://tailscale.com/install.sh | sh`
3. `sudo tailscale up`, then open the printed URL and sign in.
4. Rename the node on the admin console's Machines page if it clashes with Windows.
5. `sudo tailscale serve --bg --https=443 http://127.0.0.1:8765`

Then, either way:
- `tailscale serve status` shows the HTTPS address, `https://<machine>.<tailnet>.ts.net`.
- If Serve says HTTPS isn't enabled, enable MagicDNS and HTTPS certificates in the
  Tailscale admin console (DNS page), then run the serve command again.
- On your phone: install Tailscale, sign in to the same tailnet, open the address,
  sign in with your passphrase.
- Turn it off: `tailscale serve reset`.

Never run `tailscale funnel` for this port.

## 4. What to expect

- The list shows waiting actions, oldest first. Tap one for its full preview.
- **Approve** clears it to `ready` (nothing executes before C5). **Reject** records the
  decision and your optional feedback, and ends the run (C8 turns feedback into a
  revision task). Edit comes with C8.
- An action marked **Blocked by policy** was paused before the policy changed to
  "never". It can only be rejected.
- Tapping twice is safe: the second tap says "Already decided".
- If an answer is refused, the page says why and the run is still waiting; answer again.
- Sign-in lasts 12 hours from when you signed in. Five wrong passphrases lock sign-in
  for 15 minutes.

## 5. Troubleshooting

| Symptom | Cause |
|---|---|
| `inbox: MEASURED_OPS_INBOX_... is not set` | a secret is missing from the environment |
| `another inbox process is already running` | a second `serve`, or `--workers` > 1 |
| `PASSPHRASE_HASH can't be checked` | the hash's parameters can't run here: make a new one with `hash` |
| Phone can't load the page | the laptop is asleep or off the tailnet, or Serve is off (`tailscale serve status`) |
| Signed out unexpectedly | the inbox restarted, or 12 hours passed since sign-in |
| A run is missing from the list | it couldn't be read: the log has `can't read run <id>; skipped` |
