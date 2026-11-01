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
