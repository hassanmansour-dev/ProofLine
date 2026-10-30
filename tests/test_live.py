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


@pytest.fixture(autouse=True)
def _keys() -> None:
    load_dotenv()
    missing = [k for k in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY") if not os.environ.get(k)]
    if missing:
        pytest.skip(f"missing {missing}")


@pytest.mark.parametrize("topic", CALIBRATION_TOPICS)
async def test_pipeline_produces_sourced_script(topic: str) -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from proofline.config import get_settings
    from proofline.context import build_deps
    from proofline.graph import build_graph, initial_state, invoke_config, new_thread_id

    settings = get_settings()
    deps = build_deps(settings)
    app = build_graph(human_review=False).compile(checkpointer=InMemorySaver())
    tid = new_thread_id()
    out = await app.ainvoke(
        initial_state(topic, "developers moving into AI engineering", "short", settings),
        invoke_config(tid, settings, video_format="short"),
        context=deps,
    )
    final = out["final_script"]
    assert final.source_urls, "script cites no sources"
    assert 30 <= final.estimated_seconds <= 120
    print(
        f"\n{topic}: score={final.final_score} revisions={final.revision_count} → outputs/{tid}/script.md"
    )
