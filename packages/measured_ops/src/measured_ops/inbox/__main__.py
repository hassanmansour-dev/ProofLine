"""`python -m measured_ops.inbox {hash,seed,serve}` (C4, ADR-0017).

    hash   prompt for a passphrase, print its scrypt hash for MEASURED_OPS_INBOX_PASSPHRASE_HASH
    seed   start a few demo echo tasks so the inbox has something waiting (demo data only)
    serve  run the inbox on 127.0.0.1, one process, with the demo echo agent and policy

The host is fixed to 127.0.0.1 and there is one worker: reach it from a phone through
`tailscale serve`, never `tailscale funnel` (docs/INBOX.md). A deployment with real agents
(measured-ops-private) calls `create_inbox_app` with its own agents and policy.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from measured_ops.checkpoint import open_checkpointer
from measured_ops.contracts import Task, initial_ops_state
from measured_ops.echo import EchoAgent
from measured_ops.inbox.auth import hash_passphrase
from measured_ops.inbox.settings import PREFIX, InboxConfigError, InboxSettings
from measured_ops.policy import load_policy
from measured_ops.supervisor import build_supervisor

HOST = "127.0.0.1"  # never 0.0.0.0: the phone reaches it over Tailscale only
DEMO_GOALS = (
    "Say why every AI loop needs an exit.",
    "Explain when not to use agents, in one example.",
    "Show what an approval gate costs per run.",
)


def _policy_path() -> Path | None:
    raw = os.environ.get("MEASURED_OPS_POLICY_PATH")
    return Path(raw) if raw else None


def _checkpointer_url() -> str:
    default = InboxSettings(passphrase_hash="", session_secret="").checkpointer_url
    url = os.environ.get(f"{PREFIX}CHECKPOINTER_URL", default)
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return url


def cmd_hash() -> int:
    first = getpass.getpass("Passphrase: ")
    if len(first) < 12:
        print("Use at least 12 characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Again: ") != first:
        print("The two entries differ.", file=sys.stderr)
        return 1
    print(hash_passphrase(first))
    return 0


async def _seed(url: str, count: int) -> list[str]:
    async with open_checkpointer(url) as saver:
        graph = build_supervisor([EchoAgent()], load_policy(_policy_path())).compile(
            checkpointer=saver
        )
        threads = []
        for i in range(count):
            task = Task(agent="echo", goal=DEMO_GOALS[i % len(DEMO_GOALS)])
            thread_id = f"demo-{task.id[:12]}"
            cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke(initial_ops_state(task), cfg)
            threads.append(thread_id)
    return threads


def cmd_seed(count: int) -> int:
    for thread_id in asyncio.run(_seed(_checkpointer_url(), count)):
        print(f"paused at the gate: {thread_id}")
    return 0


def cmd_serve() -> int:
    import uvicorn

    from measured_ops.inbox.web import create_inbox_app

    _checkpointer_url()  # create the SQLite directory if needed
    try:
        settings = InboxSettings.from_env()
    except InboxConfigError as exc:
        print(f"inbox: {exc}", file=sys.stderr)
        return 2
    app = create_inbox_app(settings, [EchoAgent()], load_policy(_policy_path()))
    # One worker, enforced again by the lock file: decision locks live in this process.
    uvicorn.run(app, host=HOST, port=settings.port, workers=1, proxy_headers=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m measured_ops.inbox")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash", help="print the scrypt hash of a passphrase")
    seed = sub.add_parser("seed", help="start demo echo tasks that pause at the gate")
    seed.add_argument("--count", type=int, default=3)
    sub.add_parser("serve", help="run the inbox on 127.0.0.1 (one process)")
    args = parser.parse_args(argv)
    if args.command == "hash":
        return cmd_hash()
    if args.command == "seed":
        return cmd_seed(max(1, args.count))
    return cmd_serve()


if __name__ == "__main__":
    raise SystemExit(main())
