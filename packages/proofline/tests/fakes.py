"""Test doubles and factories. Unit tests never touch the network (CLAUDE.md)."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from proofline.config import Settings
from proofline.context import Deps
from proofline.llm import RenderedPrompt
from proofline.schemas import (
    CRITERION_WEIGHTS,
    CriterionScore,
    Critique,
    Draft,
    Finding,
    ModelRole,
    Outline,
    OutlineSection,
    ResearchNotes,
    RunConfig,
    Source,
)
from proofline.search import SearchResult, SearchUnavailableError

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMCall:
    prompt: RenderedPrompt
    schema: type[BaseModel]
    role: ModelRole
    run_config: RunConfig


class FakeLLM:
    """Returns queued responses per schema, in order. Records every call."""

    def __init__(self, responses: dict[type[BaseModel], Iterable[BaseModel]] | None = None) -> None:
        self._queues: dict[type[BaseModel], deque[BaseModel]] = defaultdict(deque)
        self.calls: list[LLMCall] = []
        for schema, items in (responses or {}).items():
            self._queues[schema].extend(items)

    def queue(self, *items: BaseModel) -> FakeLLM:
        for item in items:
            self._queues[type(item)].append(item)
        return self

    async def generate(
        self, prompt: RenderedPrompt, schema: type[T], *, role: ModelRole, run_config: RunConfig
    ) -> T:
        self.calls.append(LLMCall(prompt=prompt, schema=schema, role=role, run_config=run_config))
        queue = self._queues.get(schema)
        if not queue:
            raise AssertionError(f"FakeLLM: no queued {schema.__name__} for role={role}")
        item = queue.popleft()
        assert isinstance(item, schema)
        return item

    def calls_for(self, role: ModelRole) -> list[LLMCall]:
        return [c for c in self.calls if c.role == role]


@dataclass
class FakeSearch:
    """Canned results per query; `default` for unknown queries; `fail` raises for those queries."""

    results: dict[str, list[SearchResult]] = field(default_factory=dict)
    default: list[SearchResult] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)
    queries: list[str] = field(default_factory=list)

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        self.queries.append(query)
        if query in self.fail:
            raise SearchUnavailableError(f"FakeSearch: simulated failure for {query!r}")
        return list(self.results.get(query, self.default))[:max_results]


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    brand = tmp_path / "BRAND_VOICE.md"
    brand.write_text("Direct, practical, honest about tradeoffs.", encoding="utf-8")
    rubric = tmp_path / "RUBRIC.md"
    rubric.write_text("hook / clarity / technical_accuracy / ...", encoding="utf-8")
    values: dict[str, Any] = {
        "checkpointer_url": "memory://",
        "output_dir": tmp_path / "outputs",
        "brand_voice_path": brand,
        "rubric_path": rubric,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_deps(
    tmp_path: Path, llm: FakeLLM | None = None, search: FakeSearch | None = None, **settings: Any
) -> Deps:
    """Deps with fakes. With `search`, the researcher is a WebResearcher over it; else the stub."""
    from proofline.research import StubResearcher, WebResearcher

    s = make_settings(tmp_path, **settings)
    llm = llm or FakeLLM()
    researcher = (
        WebResearcher(search, llm, concurrency=s.search_concurrency)
        if search is not None
        else StubResearcher()
    )
    return Deps(settings=s, llm=llm, researcher=researcher)


async def run_node(
    name: str, fn: Any, state: dict[str, Any], deps: Deps, thread_id: str = "t-test"
) -> dict[str, Any]:
    """Run one node inside a real one-node graph and return exactly the update it emitted.

    Going through LangGraph (not calling fn directly) gives the node a real Runtime,
    including runtime.execution_info.thread_id. The strict-serde checkpointer makes every
    update round-trip through serialization, as it will in production.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    from proofline.checkpointing import make_serde
    from proofline.state import ScriptState

    builder = StateGraph(ScriptState, context_schema=Deps)
    builder.add_node(name, fn)
    builder.add_edge(START, name)
    builder.add_edge(name, END)
    app = builder.compile(checkpointer=InMemorySaver(serde=make_serde()))
    cfg: Any = {"configurable": {"thread_id": thread_id}}
    emitted: set[str] | None = None
    async for chunk in app.astream(state, cfg, context=deps, stream_mode="updates"):
        if name in chunk:
            emitted = set(chunk[name] or {})
    if emitted is None:
        raise AssertionError(f"node {name} emitted no update")
    # Read the update back from the checkpoint, not from memory: a state model missing
    # from the serde allowlist comes back as a dict and fails the node's type assertions.
    values = (await app.aget_state(cfg)).values
    return {k: values[k] for k in emitted}


def base_state(settings: Settings, **kw: Any) -> dict[str, Any]:
    from proofline.state import initial_state

    state: dict[str, Any] = dict(
        initial_state(
            "What LangGraph checkpointers actually store",
            "developers new to AI",
            settings.run_config(),
        )
    )
    state.update(kw)
    return state


# ── factories ─────────────────────────────────────────────────────────────────


def make_outline(**kw: Any) -> Outline:
    data: dict[str, Any] = {
        "angle": "Checkpointers store every step, so a crash costs one node, not the run.",
        "key_questions": ["What is stored?", "How do you resume?"],
        "search_queries": [
            "langgraph checkpointer",
            "langgraph interrupt resume",
            "langgraph sqlite saver",
        ],
        "sections": [
            OutlineSection(title="The crash", goal="Feel the problem"),
            OutlineSection(title="What gets saved", goal="Know what a checkpoint holds"),
            OutlineSection(title="Resume", goal="Know the one call that resumes"),
        ],
    }
    data.update(kw)
    return Outline(**data)


def make_result(n: int, *, url: str | None = None, score: float | None = None) -> SearchResult:
    return SearchResult(
        url=url or f"https://example.com/doc{n}",
        title=f"Doc {n}",
        content=f"Content of doc {n}.",
        score=score if score is not None else 1.0 - n / 100,
    )


def make_notes(n_sources: int = 3) -> ResearchNotes:
    sources = [
        Source(id=f"S{i}", url=f"https://example.com/doc{i}", title=f"Doc {i}", content=f"c{i}")
        for i in range(1, n_sources + 1)
    ]
    findings = [
        Finding(claim=f"Fact {i}.", source_ids=[f"S{i}"], confidence="high")
        for i in range(1, n_sources + 1)
    ]
    return ResearchNotes(sources=sources, findings=findings, gaps=[])


def make_draft(
    title: str = "Your agent crashed. Lose one step, not the run.",
    cited: list[str] | None = None,
    body: str | None = None,
) -> Draft:
    """Default body cites S1..S3. `cited` replaces it with one sentence per cited id."""
    if body is None:
        if cited is None:
            body = (
                "Long runs fail midway [S1].\n\n"
                "State is saved after each step [S2].\n\n"
                "Call it again with the same thread id [S3]."
            )
        else:
            body = " ".join(f"Point {i} holds [{sid}]." for i, sid in enumerate(cited, 1))
            body = body or "An uncited point."
    return Draft(
        title=title,
        hook="Your agent crashed at step nine. With a checkpointer, you lose one step"
        + (" [S1]." if cited is None else "."),
        body=body,
        call_to_action="Add a SQLite checkpointer to one graph today.",
    )


def make_critique(default: int = 8, **scores: int) -> Critique:
    return Critique(
        scores=[
            CriterionScore(
                criterion=c,
                score=scores.get(c, default),
                evidence="quote",
                fix="" if scores.get(c, default) == 10 else "do X",
            )
            for c in CRITERION_WEIGHTS
        ],
        must_fix=["clarity — “quote” — do X"],
    )
