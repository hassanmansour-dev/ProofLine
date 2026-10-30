"""SearchClient port + Tavily adapter (ADR-0003, ADR-0007)."""

from __future__ import annotations

import os
from typing import Any, Protocol

from pydantic import BaseModel


class SearchResult(BaseModel):
    url: str
    title: str
    content: str
    score: float | None = None


class SearchClient(Protocol):
    async def search(self, query: str, *, max_results: int) -> list[SearchResult]: ...


class TavilySearch:
    """Tavily returns cleaned page content, built for LLM pipelines."""

    def __init__(self, api_key: str | None = None, *, search_depth: str = "advanced") -> None:
        from tavily import AsyncTavilyClient

        key = api_key or os.environ.get("TAVILY_API_KEY")
        if not key:
            raise RuntimeError("TAVILY_API_KEY is not set (see .env.example)")
        self._client = AsyncTavilyClient(api_key=key)
        self._depth = search_depth

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        response: dict[str, Any] = await self._client.search(
            query=query, max_results=max_results, search_depth=self._depth
        )
        return [
            SearchResult(
                url=r["url"],
                title=r.get("title") or r["url"],
                content=r.get("content") or "",
                score=r.get("score"),
            )
            for r in response.get("results", [])
            if r.get("url")
        ]
