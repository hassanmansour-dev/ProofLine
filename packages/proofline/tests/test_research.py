"""The Researcher port: stub default, implementation choice, and the node's id guarantee."""

from __future__ import annotations

from pathlib import Path

import pytest

from proofline.context import Deps, build_researcher
from proofline.nodes import researcher
from proofline.research import Researcher, StubResearcher, WebResearcher
from proofline.schemas import Finding, Outline, ResearchNotes, RunConfig, Source
from tests.fakes import FakeLLM, base_state, make_outline, make_settings, run_node

pytestmark = pytest.mark.anyio


async def test_stub_returns_fixed_cited_notes_without_network(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path).run_config()
    first = await StubResearcher().research("topic one", make_outline(), cfg)
    second = await StubResearcher().research("another topic", make_outline(), cfg)
    assert first == second
    assert first.findings
    assert all(set(f.source_ids) <= first.source_ids for f in first.findings)


async def test_researcher_node_uses_whatever_researcher_is_in_deps(tmp_path: Path) -> None:
    llm = FakeLLM()
    deps = Deps(settings=make_settings(tmp_path), llm=llm, researcher=StubResearcher())
    state = base_state(deps.settings, outline=make_outline())
    update = await run_node("researcher", researcher, state, deps)
    assert update["research_notes"] == await StubResearcher().research(
        "x", make_outline(), deps.settings.run_config()
    )
    assert llm.calls == []  # the stub never calls the model


async def test_researcher_node_drops_unknown_ids_from_any_implementation(tmp_path: Path) -> None:
    class Sloppy:
        async def research(
            self, topic: str, outline: Outline, run_config: RunConfig
        ) -> ResearchNotes:
            return ResearchNotes(
                sources=[Source(id="S1", url="https://x", title="x", content="x")],
                findings=[
                    Finding(claim="kept", source_ids=["S1", "S5"], confidence="high"),
                    Finding(claim="invented", source_ids=["S9"], confidence="high"),
                ],
            )

    sloppy: Researcher = Sloppy()
    deps = Deps(settings=make_settings(tmp_path), llm=FakeLLM(), researcher=sloppy)
    update = await run_node(
        "researcher", researcher, base_state(deps.settings, outline=make_outline()), deps
    )
    [kept] = update["research_notes"].findings
    assert (kept.claim, kept.source_ids) == ("kept", ["S1"])


def test_build_researcher_defaults_to_the_stub(tmp_path: Path) -> None:
    assert isinstance(build_researcher(make_settings(tmp_path), FakeLLM()), StubResearcher)


def test_build_researcher_web_uses_tavily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")  # constructing the client makes no call
    settings = make_settings(tmp_path, researcher="web")
    assert isinstance(build_researcher(settings, FakeLLM()), WebResearcher)
