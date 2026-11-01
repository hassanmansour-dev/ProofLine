"""Graph wiring — no business logic here.

Week 1 S5 (linear):   START → planner → researcher → writer → critic → finalizer → END
Week 2 S1 (loop):     critic ─route_after_critic→ {"writer": writer, "human_review": <review target>}
Week 3 S1 (human):    human_review ─route_after_human→ {writer, finalizer}

`human_review=False` (CLI --no-review, Week 1-2 tests) maps the router's "human_review"
outcome straight to finalizer and doesn't add the node — same graph otherwise.

Rules: StateGraph(ScriptState, context_schema=Deps, input_schema=InputState);
path maps on every conditional edge; researcher gets researcher.RETRY_POLICY
(retries only SearchUnavailableError — ADR-0011; not a bare RetryPolicy).
"""

from __future__ import annotations

import argparse
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph

from proofline.config import Settings
from proofline.prompts import prompt_versions
from proofline.state import InputState, ScriptState


def build_graph(
    *, human_review: bool = True
) -> StateGraph[ScriptState, Any, InputState, ScriptState]:
    """Return the uncompiled builder; callers compile with their checkpointer."""
    raise NotImplementedError("Week 1 S5 — see module docstring")


def new_thread_id() -> str:
    return str(uuid.uuid4())


def invoke_config(thread_id: str, settings: Settings) -> RunnableConfig:
    """Config for every invoke/resume: identity, recursion backstop, tracing metadata."""
    cfg = settings.run_config()
    return RunnableConfig(
        configurable={"thread_id": thread_id},
        recursion_limit=settings.recursion_limit,
        run_name="script-pipeline",
        tags=[settings.env],
        metadata={
            "thread_id": thread_id,
            "prompt_versions": prompt_versions(),
            "models": {role: spec.model for role, spec in cfg.models.items()},
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the graph")
    parser.add_argument("--mermaid", action="store_true", help="print the mermaid diagram")
    args = parser.parse_args()
    if args.mermaid:
        print(build_graph().compile().get_graph().draw_mermaid())  # noqa: T201


if __name__ == "__main__":
    main()
