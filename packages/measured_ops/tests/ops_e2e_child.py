"""First half of the e2e run, in its own process: task in → echo agent → pause at the gate.

Run by test_ops_e2e.py as
`python ops_e2e_child.py <checkpointer url> <thread id> [<action type> <goal>]`.
Prints the interrupt payload as JSON and exits, so the test's resume can only come
from the checkpoint, never from memory.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from measured_ops import Task, initial_ops_state, load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.echo import EchoAgent
from measured_ops.supervisor import build_supervisor

DEFAULT_GOAL = "Say why every AI loop needs an exit."


async def main(
    url: str, thread_id: str, action_type: str = "publish_public", goal: str = DEFAULT_GOAL
) -> None:
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    task = Task(agent="echo", goal=goal)
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    async with open_checkpointer(url) as saver:
        app = build_supervisor([EchoAgent(action_type)], load_policy()).compile(checkpointer=saver)
        out = await app.ainvoke(initial_ops_state(task), cfg)
    [intr] = out["__interrupt__"]
    print(json.dumps(intr.value))


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
