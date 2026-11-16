"""Live smoke tests — real Anthropic + Tavily calls. Opt in: `uv run pytest -m live`.

Calibration topics (Week 2 S4) live here so every prompt change runs the same set.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

pytestmark = [pytest.mark.live, pytest.mark.anyio]

CALIBRATION_TOPICS = [
    "What LangGraph checkpointers actually store",
    "Why your RAG answers get worse as you add documents",
    "Structured output vs tool calling for extraction",
]


def _require(*keys: str) -> None:
    load_dotenv()
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        pytest.skip(f"missing {missing}")


async def test_brief_topic_with_stub_research() -> None:
    """The Week 1 done-when run: real model, fixed research notes."""
    from proofline.cli import run_pipeline
    from proofline.config import Settings
    from proofline.context import build_deps

    _require("ANTHROPIC_API_KEY")
    deps = build_deps(Settings(researcher="stub"))
    final = await run_pipeline("Why every AI loop needs an exit", "developers new to AI", deps)
    assert final.source_urls, "script cites no sources"
    assert final.word_count <= deps.settings.max_words
    print(f"\n{final.markdown}")


@pytest.mark.parametrize("topic", CALIBRATION_TOPICS)
async def test_web_research_produces_sourced_script(topic: str) -> None:
    from proofline.cli import run_pipeline
    from proofline.config import Settings
    from proofline.context import build_deps

    _require("ANTHROPIC_API_KEY", "TAVILY_API_KEY")
    deps = build_deps(Settings(researcher="web"))
    final = await run_pipeline(topic, "developers new to AI", deps)
    assert final.source_urls, "script cites no sources"
    assert final.word_count <= deps.settings.max_words
    print(f"\n{topic}: {final.word_count} words → outputs/{final.thread_id}/script.md")


async def test_content_agent_drafts_one_topic_into_the_gate(tmp_path: object) -> None:
    """C8 done-when, in code: a real draft through the content agent, eval and gate.

    Real model, stub research. The draft waits at the gate; nothing is published."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from measured_ops import Task, initial_ops_state, load_policy
    from measured_ops.contracts import SERDE_ALLOWLIST
    from measured_ops.supervisor import build_supervisor
    from proofline.agent import APPROVE_DRAFT, ContentAgent
    from proofline.config import Settings
    from proofline.context import build_deps

    _require("ANTHROPIC_API_KEY")
    deps = build_deps(Settings(researcher="stub"))
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    graph = build_supervisor([ContentAgent(deps)], load_policy()).compile(checkpointer=saver)
    task = Task(
        agent="content",
        goal="Draft a LinkedIn post, a 1-minute clip script and a lesson outline.",
        inputs={"topic": "Why every AI loop needs an exit", "audience": "developers new to AI"},
    )

    out = await graph.ainvoke(
        initial_ops_state(task), {"configurable": {"thread_id": "live-c8"}, "recursion_limit": 40}
    )

    [intr] = out["__interrupt__"]
    assert intr.value["type"] == APPROVE_DRAFT
    assert out["results"][0].status == "succeeded"
    assert out["usage"], "no usage recorded for a real model call"
    print(f"\n{out['results'][0].summary}\n\n{intr.value['preview']}")
