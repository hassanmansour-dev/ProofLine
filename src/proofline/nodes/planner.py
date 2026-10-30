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

import logging
import time
from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import load_prompt
from proofline.schemas import TARGET_SECONDS, Plan
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.planner")


async def planner(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    started = time.perf_counter()
    target_seconds = TARGET_SECONDS[state["video_format"]]
    prompt = load_prompt("planner").render(
        topic=state["topic"],
        audience=state["audience"],
        video_format=state["video_format"],
        target_seconds=target_seconds,
    )
    plan = await runtime.context.llm.generate(
        prompt, Plan, role="planner", run_config=state["run_config"]
    )
    log.info(
        "planner: %d queries, %d sections, target %ss (%.1fs)",
        len(plan.search_queries),
        len(plan.outline),
        plan.target_seconds,
        time.perf_counter() - started,
    )
    return {"plan": plan}
