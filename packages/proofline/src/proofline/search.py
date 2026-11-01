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


class SearchUnavailableError(Exception):
    """A transient search failure (timeout, 5xx, network) worth retrying.

    The researcher's RetryPolicy retries only this. Auth, quota and bad-request errors
    propagate as they are: retrying them wastes time and quota.
    """


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
        import httpx
        from tavily.errors import TimeoutError as TavilyTimeoutError

        try:
            response: dict[str, Any] = await self._client.search(
                query=query, max_results=max_results, search_depth=self._depth
            )
        except (TavilyTimeoutError, httpx.TransportError) as exc:
            raise SearchUnavailableError(f"tavily: {exc!r}") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise SearchUnavailableError(f"tavily: HTTP {exc.response.status_code}") from exc
            raise
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
