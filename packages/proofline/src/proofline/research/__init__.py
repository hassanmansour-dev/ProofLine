"""The Researcher port: topic + outline → ResearchNotes (ADR-0012).

The graph's researcher node depends only on this protocol, so the implementation
can change (stub today, a web-search agent in Week 5) without touching graph.py.
Implementations assign source ids S1..Sn; the node still drops findings that cite
ids it doesn't know, so every implementation gets the same guarantee (ADR-0007).
"""

from __future__ import annotations

from typing import Protocol

from proofline.research.stub import StubResearcher
from proofline.research.web import WebResearcher
from proofline.schemas import Outline, ResearchNotes, RunConfig


class Researcher(Protocol):
    async def research(
        self, topic: str, outline: Outline, run_config: RunConfig
    ) -> ResearchNotes: ...


__all__ = ["Researcher", "StubResearcher", "WebResearcher"]
