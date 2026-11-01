"""Graph wiring — no business logic here.

Week 1 (now):      START → planner → researcher → writer → finalizer → END
Week 2 (loop):     writer → critic ─route_after_critic→ {writer, human_review}
Week 3 (human):    human_review ─route_after_human→ {writer, finalizer}

Rules: StateGraph(ScriptState, context_schema=Deps); path maps on every conditional
edge; the researcher gets researcher.RETRY_POLICY (retries only SearchUnavailableError,
ADR-0011). The input schema is the full ScriptState, not InputState: a narrower input
schema would drop the defaults initial_state() sets for later-week fields.
"""

from __future__ import annotations

import argparse
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from proofline.config import Settings
from proofline.context import Deps
from proofline.nodes import finalizer, planner, researcher, writer
from proofline.nodes.researcher import RETRY_POLICY
from proofline.prompts import prompt_versions
from proofline.state import ScriptState

# Week 1 runs these, in this order.
LINEAR_NODES = ("planner", "researcher", "writer", "finalizer")


def build_graph() -> StateGraph[ScriptState, Deps, ScriptState, ScriptState]:
    """Return the uncompiled builder; callers compile, with a checkpointer from Week 3."""
    builder: StateGraph[ScriptState, Deps, ScriptState, ScriptState] = StateGraph(
        ScriptState, context_schema=Deps
    )
    builder.add_node("planner", planner)
    builder.add_node("researcher", researcher, retry_policy=RETRY_POLICY)
    builder.add_node("writer", writer)
    builder.add_node("finalizer", finalizer)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "researcher")
    builder.add_edge("researcher", "writer")
    builder.add_edge("writer", "finalizer")
    builder.add_edge("finalizer", END)
    return builder


def new_thread_id() -> str:
    return str(uuid.uuid4())


def invoke_config(thread_id: str, settings: Settings) -> RunnableConfig:
    """Config for every invoke/resume: identity, recursion backstop, tracing metadata."""
    cfg = settings.run_config()
    metadata: dict[str, Any] = {
        "thread_id": thread_id,
        "prompt_versions": prompt_versions(),
        "models": {role: spec.model for role, spec in cfg.models.items()},
    }
    return RunnableConfig(
        configurable={"thread_id": thread_id},
        recursion_limit=settings.recursion_limit,
        run_name="script-pipeline",
        tags=[settings.env],
        metadata=metadata,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the graph")
    parser.add_argument("--mermaid", action="store_true", help="print the mermaid diagram")
    args = parser.parse_args()
    if args.mermaid:
        print(build_graph().compile().get_graph().draw_mermaid())  # noqa: T201


if __name__ == "__main__":
    main()
