"""First half of the C5 e2e run, in its own process (test_tools_e2e.py).

`python tools_e2e_child.py <checkpointer url> <thread id> <auto|approval>`

The agent reads the demo tracker over MCP (stdio, the public demo config), proposes
moving the first "To Do" issue to "In Progress", and the run either executes it now
("auto": the demo policy) or pauses at the gate ("approval": a policy that asks for ticket
moves). Prints the interrupt payload, or the outcomes, as JSON. The demo tracker's state
file comes from MEASURED_OPS_DEMO_TOOLS_STATE, set by the test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from tool_fakes import TriageStubAgent

from measured_ops import Task, initial_ops_state
from measured_ops.checkpoint import open_checkpointer
from measured_ops.policy import ApprovalPolicy, load_policy
from measured_ops.supervisor import build_supervisor
from measured_ops.tools.config import load_tools_config
from measured_ops.tools.mcp import build_executors, build_read_tools

GOAL = "Triage this week's tickets."


def policy_for(mode: str) -> ApprovalPolicy:
    demo = load_policy()
    if mode == "auto":
        return demo
    return ApprovalPolicy(version=1, actions={**demo.actions, "triage_ticket": "approval"})


def supervisor(mode: str) -> Any:
    config = load_tools_config()
    jira = build_read_tools(config).jira
    assert jira is not None
    return build_supervisor(
        [TriageStubAgent(jira)], policy_for(mode), executors=build_executors(config)
    )


async def main(url: str, thread_id: str, mode: str) -> None:
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    cfg: Any = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    async with open_checkpointer(url) as saver:
        app = supervisor(mode).compile(checkpointer=saver)
        out = await app.ainvoke(initial_ops_state(Task(agent="ops", goal=GOAL)), cfg)
    if "__interrupt__" in out:
        [intr] = out["__interrupt__"]
        print(json.dumps({"paused": intr.value}))
    else:
        print(json.dumps({"executed": [o.model_dump(mode="json") for o in out["executed"]]}))


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
