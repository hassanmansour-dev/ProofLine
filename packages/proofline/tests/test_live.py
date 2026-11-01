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
    from proofline.graph import build_graph, invoke_config, new_thread_id
    from proofline.state import initial_state

    settings = get_settings()
    deps = build_deps(settings)
    app = build_graph(human_review=False).compile(checkpointer=InMemorySaver())
    tid = new_thread_id()
    out = await app.ainvoke(
        initial_state(topic, "developers new to AI", settings.run_config()),
        invoke_config(tid, settings),
        context=deps,
    )
    final = out["final_script"]
    assert final.source_urls, "script cites no sources"
    assert final.word_count <= settings.max_words
    print(
        f"\n{topic}: score={final.final_score} revisions={final.revision_count} → outputs/{tid}/script.md"
    )
