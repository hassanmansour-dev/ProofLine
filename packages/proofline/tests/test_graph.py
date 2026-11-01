"""Graph-level acceptance tests, Weeks 1-3. Remove each xfail marker as the week lands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from proofline.checkpointing import open_checkpointer
from proofline.context import Deps
from proofline.graph import LINEAR_NODES, build_graph, invoke_config
from proofline.schemas import ExtractedFindings, Finding, HumanDecision
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

# Week 1 wiring is linear, so these fail on assertions until their week lands.
W2 = pytest.mark.xfail(strict=True, reason="Week 2 target")
W3 = pytest.mark.xfail(strict=True, reason="Week 3 target")


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


# ── Week 1 ────────────────────────────────────────────────────────────────────


async def test_linear_run_end_to_end_with_fake_model(tmp_path: Path) -> None:
    llm = FakeLLM().queue(make_outline(), make_draft())
    deps = make_deps(tmp_path, llm)  # stub researcher: no network, no LLM call
    app = build_graph().compile()  # Week 1: no checkpointer
    cfg = invoke_config("w1", deps.settings)
    updates = [
        chunk
        async for chunk in app.astream(_inputs(deps), cfg, context=deps, stream_mode="updates")
    ]
    assert tuple(node for chunk in updates for node in chunk) == LINEAR_NODES
    assert [c.role for c in llm.calls] == ["planner", "writer"]

    final = updates[-1]["finalizer"]["final_script"]
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


@W2
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


@W2
async def test_critic_loop_stops_at_revision_budget(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _llm([5, 5, 5, 5]))  # max_revisions=3 → 4 drafts, then stop
    app = build_graph().compile(checkpointer=InMemorySaver())
    cfg = invoke_config("w2b", deps.settings)
    nodes = await _stream(app, _inputs(deps), cfg, deps)
    assert nodes.count("writer") == 4
    assert nodes[-1] == "finalizer"


# ── Week 3 ────────────────────────────────────────────────────────────────────


@W3
async def test_reject_then_approve(tmp_path: Path) -> None:
    llm = _llm([9, 9])
    deps = _deps(tmp_path, llm)
    app = build_graph().compile(checkpointer=InMemorySaver())
    cfg = invoke_config("w3a", deps.settings)

    first = await app.ainvoke(_inputs(deps), cfg, context=deps)
    [intr] = first["__interrupt__"]
    assert intr.value["score"] == make_critique(9).overall

    reject = HumanDecision(approved=False, feedback="Open with the crash story.")
    second = await app.ainvoke(Command(resume=reject.model_dump()), cfg, context=deps)
    assert "__interrupt__" in second
    assert "<human_feedback>" in llm.calls_for("writer")[-1].prompt.user

    done = await app.ainvoke(
        Command(resume=HumanDecision(approved=True).model_dump()), cfg, context=deps
    )
    assert done["final_script"].revision_count == 1
    assert [h.kind for h in done["history"]].count("human") == 2


@W3
async def test_resume_after_process_restart_sqlite(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'cp.db'}"
    deps = _deps(tmp_path, _llm([9]))
    cfg = invoke_config("w3b", deps.settings)

    async with open_checkpointer(url) as saver:
        await build_graph().compile(checkpointer=saver).ainvoke(_inputs(deps), cfg, context=deps)

    async with open_checkpointer(url) as saver:  # "new process"
        app = build_graph().compile(checkpointer=saver)
        snap = await app.aget_state(cfg)
        assert snap.next == ("human_review",)
        done = await app.ainvoke(
            Command(resume=HumanDecision(approved=True).model_dump()), cfg, context=deps
        )
    assert done["final_script"].thread_id == "w3b"
