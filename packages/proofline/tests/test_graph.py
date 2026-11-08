"""Graph-level acceptance tests. Remove each xfail marker when its docs/PLAN.md step lands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from proofline.context import Deps
from proofline.graph import LINEAR_NODES, build_graph, invoke_config
from proofline.schemas import ExtractedFindings, Finding
from proofline.state import initial_state
from tests.fakes import (
    FakeLLM,
    FakeSearch,
    make_critique,
    make_deps,
    make_draft,
    make_outline,
    make_result,
)

pytestmark = pytest.mark.anyio

# The wiring is linear today, so these fail on assertions until their step lands.
C8_TARGET = pytest.mark.xfail(strict=True, reason="C8 target")


def _llm(critic_scores: list[int], drafts: int | None = None) -> FakeLLM:
    findings = ExtractedFindings(
        findings=[
            Finding(claim=f"Fact {i}.", source_ids=[f"S{i}"], confidence="high") for i in (1, 2, 3)
        ]
    )
    llm = FakeLLM().queue(make_outline(), findings)
    for _ in range(drafts or len(critic_scores)):
        llm.queue(make_draft())
    for s in critic_scores:
        llm.queue(make_critique(s))
    return llm


def _deps(tmp_path: Path, llm: FakeLLM) -> Deps:
    return make_deps(
        tmp_path, llm, FakeSearch(default=[make_result(1), make_result(2), make_result(3)])
    )


async def _stream(app: Any, inputs: Any, cfg: Any, deps: Deps) -> list[str]:
    nodes: list[str] = []
    async for chunk in app.astream(inputs, cfg, context=deps, stream_mode="updates"):
        nodes.extend(chunk)
    return nodes


def _inputs(deps: Deps) -> Any:
    return initial_state(
        "What LangGraph checkpointers actually store", "devs", deps.settings.run_config()
    )


# ── Linear flow ────────────────────────────────────────────────────────────────────


async def test_linear_run_end_to_end_with_fake_model(tmp_path: Path) -> None:
    llm = FakeLLM().queue(make_outline(), make_draft())
    deps = make_deps(tmp_path, llm)  # stub researcher: no network, no LLM call
    app = build_graph().compile()  # linear flow: no checkpointer
    cfg = invoke_config("w1", deps.settings)
    updates: list[dict[str, Any]] = []
    values: dict[str, Any] = {}
    stream = app.astream(_inputs(deps), cfg, context=deps, stream_mode=["updates", "values"])
    async for mode, data in stream:
        if mode == "updates":
            updates.append(data)
        else:
            values = data
    assert tuple(node for chunk in updates for node in chunk) == LINEAR_NODES
    assert [c.role for c in llm.calls] == ["planner", "writer"]
    # initial_state() defaults survive the graph's input schema (see graph.py)
    assert values["feedback"] is None
    assert values["critique"] is None
    assert values["score"] is None

    final = values["final_script"]
    assert final.thread_id == "w1"
    assert final.word_count <= deps.settings.max_words
    assert final.markdown.startswith(f"# {make_draft().title}")
    assert final.source_urls  # the draft cites the stub's sources
    assert (deps.settings.output_dir / "w1" / "script.md").read_text() == final.markdown


async def test_mermaid_renders_the_week1_nodes() -> None:
    mermaid = build_graph().compile().get_graph().draw_mermaid()
    for node in LINEAR_NODES:
        assert node in mermaid


# ── Week 2 ────────────────────────────────────────────────────────────────────


@C8_TARGET
async def test_critic_loop_revises_until_threshold(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _llm([6, 7, 8]))
    app = build_graph().compile(checkpointer=InMemorySaver())
    cfg = invoke_config("w2a", deps.settings)
    nodes = await _stream(app, _inputs(deps), cfg, deps)
    assert nodes.count("writer") == 3
    assert nodes[-1] == "finalizer"
    state = (await app.aget_state(cfg)).values
    assert state["revision_count"] == 2
    assert [h.kind for h in state["history"]].count("critique") == 3


@C8_TARGET
async def test_critic_loop_stops_at_revision_budget(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _llm([5, 5, 5, 5]))  # max_revisions=3 → 4 drafts, then stop
    app = build_graph().compile(checkpointer=InMemorySaver())
    cfg = invoke_config("w2b", deps.settings)
    nodes = await _stream(app, _inputs(deps), cfg, deps)
    assert nodes.count("writer") == 4
    assert nodes[-1] == "finalizer"
