"""Runtime dependencies passed to every node via LangGraph's context_schema (ADR-0003).

Context is not checkpointed — pass it again on every invoke/resume.
"""

from __future__ import annotations

from dataclasses import dataclass

from proofline.config import Settings, get_settings
from proofline.llm import AnthropicLLM, StructuredLLM
from proofline.research import Researcher, StubResearcher, WebResearcher


@dataclass(frozen=True)
class Deps:
    settings: Settings
    llm: StructuredLLM
    researcher: Researcher


def build_researcher(settings: Settings, llm: StructuredLLM) -> Researcher:
    """PROOFLINE_RESEARCHER picks the implementation; only `web` needs TAVILY_API_KEY."""
    if settings.researcher == "web":
        from proofline.search import TavilySearch

        return WebResearcher(TavilySearch(), llm, concurrency=settings.search_concurrency)
    return StubResearcher()


def build_deps(settings: Settings | None = None) -> Deps:
    """Production wiring. Tests build Deps directly with fakes."""
    settings = settings or get_settings()
    llm = AnthropicLLM()
    return Deps(settings=settings, llm=llm, researcher=build_researcher(settings, llm))
