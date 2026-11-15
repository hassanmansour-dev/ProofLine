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
import sys

from langchain_core.runnables import RunnableConfig

from measured_ops.checkpoint import open_checkpointer
from measured_ops.contracts import Task, initial_ops_state
from measured_ops.echo import EchoAgent
from measured_ops.inbox.auth import hash_passphrase
from measured_ops.inbox.settings import checkpointer_url, env_path
from measured_ops.policy import load_policy
from measured_ops.supervisor import build_supervisor
from measured_ops.tracing import masked_tracing_callbacks

DEMO_GOALS = (
    "Say why every AI loop needs an exit.",
    "Explain when not to use agents, in one example.",
    "Show what an approval gate costs per run.",
)


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
        graph = build_supervisor(
            [EchoAgent()], load_policy(env_path("MEASURED_OPS_POLICY_PATH"))
        ).compile(checkpointer=saver)
        threads = []
        for i in range(count):
            task = Task(agent="echo", goal=DEMO_GOALS[i % len(DEMO_GOALS)])
            thread_id = f"demo-{task.id[:12]}"
            cfg: RunnableConfig = {
                "configurable": {"thread_id": thread_id},
                "callbacks": masked_tracing_callbacks(),  # PII masked in traces (ADR-0018)
            }
            await graph.ainvoke(initial_ops_state(task), cfg)
            threads.append(thread_id)
    return threads


def cmd_seed(count: int) -> int:
    for thread_id in asyncio.run(_seed(checkpointer_url(), count)):
        print(f"paused at the gate: {thread_id}")
    return 0


def cmd_serve() -> int:
    from measured_ops.inbox.server import serve  # needs the `inbox` extra

    return serve([EchoAgent()])


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
