"""First half of the content-agent e2e, in its own process (C8, ADR-0019).

`python -m tests.content_e2e_child <checkpointer url> <thread id> <tmp dir>` from
packages/proofline: one topic → content agent (fake LLM, stub researcher) → eval →
pause at the gate. Prints the interrupt payload as JSON and exits, so the test's resume
can only come from the checkpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from measured_ops import Task, initial_ops_state, load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.supervisor import build_supervisor
from proofline.agent import ContentAgent
from tests.fakes import FakeLLM, make_critique, make_deps, make_draft, make_outline

TOPIC = "Why every AI loop needs an exit"


async def main(url: str, thread_id: str, tmp: str) -> None:
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    llm = FakeLLM().queue(make_outline(), make_draft(), make_critique(9))
    agent = ContentAgent(make_deps(Path(tmp), llm))
    task = Task(
        agent="content",
        goal="Draft a LinkedIn post, a 1-minute clip script and a lesson outline.",
        inputs={"topic": TOPIC, "audience": "developers new to AI"},
    )
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    async with open_checkpointer(url) as saver:
        app = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
        out = await app.ainvoke(initial_ops_state(task), cfg)
    [intr] = out["__interrupt__"]
    print(json.dumps(intr.value))


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
