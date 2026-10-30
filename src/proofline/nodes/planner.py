"""planner — topic → Plan. Week 1, S2.

Contract (docs/ARCHITECTURE.md §4):
    reads:   topic, audience, video_format, run_config
    writes:  plan
    LLM:     prompts/planner.md, role="planner", schema=Plan
Steps:
    1. target_seconds = schemas.TARGET_SECONDS[state["video_format"]]
    2. load_prompt("planner").render(topic=..., audience=..., video_format=..., target_seconds=...)
    3. plan = await runtime.context.llm.generate(prompt, Plan, role="planner", run_config=state["run_config"])
    4. log INFO: duration, #queries, #sections
    5. return {"plan": plan}
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.state import ScriptState


async def planner(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    raise NotImplementedError("Week 1 S2 — see module docstring")
