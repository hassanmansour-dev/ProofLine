"""Runtime dependencies passed to every node via LangGraph's context_schema (ADR-0003).

Context is not checkpointed — pass it again on every invoke/resume.
"""

from __future__ import annotations

from dataclasses import dataclass

from proofline.config import Settings, get_settings
from proofline.llm import AnthropicLLM, StructuredLLM
from proofline.search import SearchClient, TavilySearch


@dataclass(frozen=True)
class Deps:
    settings: Settings
    llm: StructuredLLM
    search: SearchClient


def build_deps(settings: Settings | None = None) -> Deps:
    """Production wiring. Tests build Deps directly with fakes."""
    return Deps(settings=settings or get_settings(), llm=AnthropicLLM(), search=TavilySearch())
