# Deploying Measured Ops (C10 proposal)

Status: proposal on branch `c10/deploy-proposal`. Nothing here has been run against a real
host. Deploying is a `deploy_production` action: it needs Hassan's approval (approval
policy), and every step that creates an account, buys something or opens a sign-in is his.
Where to run it and what it costs: `hosting-options.md` (team notes, 2026-11-24).

What runs (ADR-0022, ADR-0023, docs/INBOX.md): the inbox (`proofline inbox`, `127.0.0.1:8765`,
one process), the worker (`python -m measured_ops.worker run`: digests at 07:00 and 21:00
America/Toronto, runs no agent), Postgres (the supervisor's checkpoints and the judge log /
posted marks, in two databases), and the kill switch file.

Rules that hold on both paths:
- **The inbox is never public.** It binds `127.0.0.1`; nothing publishes a port; the phone
  reaches it over Tailscale Serve (tailnet only). Never `tailscale funnel`.
- **No secret in git.** Values go in `.env` (mode 600). The real config (tools, policy,
  prices, budget) comes from `measured-ops-private` and is copied to the host, never into
  this repo.
- Set a budget (`MEASURED_OPS_BUDGET_PATH`) before the first unattended run; no path means no caps.
- Restart the inbox and worker after every `git pull` or config change: a running process keeps
  what it started with.

## Path 1: a small VPS (recommended in hosting-options.md): Docker Compose

Files: `deploy/Dockerfile`, `deploy/compose.yaml`, `deploy/env.template`,
`deploy/postgres-init/01-databases.sh`.

### Hassan does (accounts and purchases)
1. Order the VPS (OVHcloud VPS-1, Beauharnois, Ubuntu 24.04 or Debian 12). Add an SSH key; no
   password login. Turn on the provider's two-factor sign-in.
2. Create a Tailscale auth key (admin console, Settings, Keys; a **reusable: no**, expiring
   key is enough for one machine). If you use an OAuth client secret instead, Tailscale requires
   a tag: `TS_EXTRA_ARGS=--advertise-tags=tag:...`. Enable MagicDNS and HTTPS certificates (DNS
   page) if Serve asks for them.

### On the host
```bash
# 1. Docker (use Docker's own install instructions for the distro), then:
sudo systemctl enable --now docker
# 2. Code
git clone https://github.com/hassanmansour-dev/proofline.git && cd proofline
git checkout c10/deploy-proposal      # or the branch that merged it
cd deploy
# 3. Environment
cp env.template .env && chmod 600 .env       # fill in every empty name; see below
openssl rand -hex 24                          # a POSTGRES_PASSWORD (hex: it goes in a URL)
# 4. Real config from measured-ops-private (do not commit ./private)
mkdir -p private && chmod 700 private
#   copy tools.toml, approval_policy.toml, model_prices.toml, budget.toml into ./private
#   (file names must match the *_PATH values in .env)
```
Fill `.env`: `ANTHROPIC_API_KEY`; the inbox passphrase hash and session secret (docs/INBOX.md
section 1: `docker compose run --rm inbox python -m measured_ops.inbox hash`, and
`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`); the three tool-credential
names from measured-ops-private `docs/TOOLS_SETUP.md`; `TS_AUTHKEY`.

### First start
```bash
docker compose --env-file .env build
docker compose --env-file .env up -d postgres
docker compose --env-file .env ps          # postgres: healthy (the init script made ops_checkpoints and ops_logs)

# Outlook sign-in, once (needs ./private/tools.toml and the Azure app from TOOLS_SETUP.md).
# The container has no keyring, so this uses the opt-in cleartext token cache on the `outlook`
# volume: your decision, see TOOLS_SETUP.md step 6. Write the config, then sign in:
docker compose run --rm inbox sh -c 'cat > /home/ops/.outlook-mcp/config.json <<EOF2
{"client_id": "'"$OUTLOOK_MCP_CLIENT_ID"'", "tenant_id": "consumers", "timezone": "America/Toronto",
 "read_only": false, "allow_categories": ["calendar_write"], "allow_unencrypted_token_cache": true}
EOF2
chmod 600 /home/ops/.outlook-mcp/config.json
uvx --from outlook-graph-mcp==1.33.0 outlook-mcp auth'
# open the printed URL on any device and enter the code

# Check the tools config against the live servers (lists tools, calls none):
docker compose run --rm inbox python -m measured_ops.tools check --config /config/tools.toml

docker compose --env-file .env up -d      # inbox, worker, tailscale
docker compose exec tailscale tailscale serve --bg --https=443 http://127.0.0.1:8765
docker compose exec tailscale tailscale serve status     # prints https://<TS_HOSTNAME>.<tailnet>.ts.net
```
Pass looks like: `tools check` prints seven `ok` lines; `docker compose ps` shows inbox
`healthy` and the others `running`; opening the Serve address on your phone (Tailscale app, same
tailnet) shows the sign-in page, and your passphrase gets you the list. Test that it is private:
from a device not on your tailnet, the address does not load, and `ss -ltn` on the host shows
nothing listening on 8765 outside the container's own namespace.

The Serve setting is kept in the `tsstate` volume, so it survives restarts. Turn it off with
`docker compose exec tailscale tailscale serve reset`.

### Day to day
```bash
docker compose exec worker python -m measured_ops.worker status          # kill switch, budget, logs backend
docker compose exec worker python -m measured_ops.worker kill --reason "why"
docker compose exec worker python -m measured_ops.worker release
docker compose logs --since 24h inbox worker | less
ls $(docker volume inspect measured-ops_outputs -f '{{.Mountpoint}}')/digests   # digests (as root)
```
Update: `git pull`, `docker compose --env-file .env up -d --build`. Roll back: check out the
previous commit and rebuild; the databases are untouched.

Backups (the provider's daily VPS backup is not a database dump):
```bash
docker compose exec -T postgres pg_dump -U ops -Fc ops_checkpoints > ops_checkpoints-$(date +%F).dump
docker compose exec -T postgres pg_dump -U ops -Fc ops_logs        > ops_logs-$(date +%F).dump
```
Copy the dumps off the host (they hold run state and decisions, no secrets). Restore into an
empty database with `pg_restore -U ops -d <db> --clean --if-exists`.

## Path 2: the Ubuntu machine: systemd user units

Files: `deploy/systemd/measured-ops-inbox.service`, `measured-ops-worker.service`,
`measured-ops-no-sleep.conf`, `measured-ops-logind.conf`.

Assumptions to check first: the private checkout is `~/measured-ops-private` and `uv` is
`~/.local/bin/uv` (edit the units if not); Postgres is installed locally with two databases
(or use SQLite for a trial: leave the two URLs unset).

```bash
# Environment file, mode 600, outside any repo
mkdir -p ~/.config/measured-ops
cp deploy/env.template ~/.config/measured-ops/env && chmod 600 ~/.config/measured-ops/env
#   plain KEY=value lines only (no quotes, no `export`); set the two database URLs and the
#   *_PATH values to real absolute paths under the private checkout (not /config)
#   and MEASURED_OPS_KILL_SWITCH_PATH to an absolute path, e.g. /home/<you>/.measured_ops/kill_switch

# Units
mkdir -p ~/.config/systemd/user
cp deploy/systemd/measured-ops-*.service ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now measured-ops-inbox measured-ops-worker
systemctl --user status measured-ops-inbox measured-ops-worker

# Keep the machine awake (needs sudo; read the comments in the files first)
sudo install -D -m 644 deploy/systemd/measured-ops-no-sleep.conf /etc/systemd/sleep.conf.d/measured-ops.conf
sudo install -D -m 644 deploy/systemd/measured-ops-logind.conf   /etc/systemd/logind.conf.d/measured-ops.conf
sudo systemctl restart systemd-logind     # ends graphical sessions: run it from SSH
```
Then Tailscale Serve exactly as in docs/INBOX.md section 3. Logs: `journalctl --user -u
measured-ops-inbox -f`. Status and kill switch: the `worker` commands above, run with `uv run`
in the private checkout. Update: `git pull`, `uv sync`, `systemctl --user restart measured-ops-inbox
measured-ops-worker`.

Known limits on this path: the Outlook sign-in uses the OS keyring, which a user unit can reach
only while the user's session bus is up (linger helps; after a reboot it may stay locked until
you log in), so decide on the opt-in cleartext cache as in TOOLS_SETUP.md step 6; home power and
internet are yours to keep up.

## The three unattended days (ADR-0023's done condition)

Before starting the clock:
- [ ] `worker status` shows the budget file you expect and the logs backend `postgres`
- [ ] kill switch tested: `kill`, see the inbox refuse Retry and the page say so, then `release`
- [ ] `tools check` passes; one approved calendar block lands on the right calendar
- [ ] phone opens the inbox over Tailscale, off Wi-Fi
- [ ] a Postgres dump taken and restored into a scratch database once
- [ ] reboot test: reboot the host, wait, confirm inbox, worker and the Serve address return unaided

Each day: a digest exists for 07:00 and for 21:00 in `outputs/digests/`; `worker status` is
unchanged; nothing in the logs says `can't read run` or `Stopped`. The worker writes files only,
so a phone notification for digests is not in place: that channel is still Hassan's decision
(ADR-0023).

## Not done here
No deploy, no account, no purchase. Not verified in this sandbox: the image build (no Docker
daemon), the systemd units (no systemd), and the Tailscale container against a real tailnet.
