"""planner — topic + audience → Outline.

Contract (docs/ARCHITECTURE.md §4):
    reads:   topic, audience, run_config
    writes:  outline
    LLM:     prompts/planner.md, role="planner", schema=Outline
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import load_prompt
from proofline.schemas import Outline
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.planner")


async def planner(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    started = time.perf_counter()
    cfg = state["run_config"]
    prompt = load_prompt("planner").render(
        brand=cfg.brand_name,
        topic=state["topic"],
        audience=state["audience"],
        max_words=cfg.max_words,
    )
    outline = await runtime.context.llm.generate(prompt, Outline, role="planner", run_config=cfg)
    log.info(
        "planner: %d queries, %d beats (%.1fs)",
        len(outline.search_queries),
        len(outline.sections),
        time.perf_counter() - started,
    )
    return {"outline": outline}
