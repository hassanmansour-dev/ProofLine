"""Tool ports (C5, ADR-0022): what agents may read, and what only executors may write.

Agents get the read ports (`JiraReads`, `CalendarReads`) through their constructor, like
any dependency. The write ports exist only inside the executors the supervisor holds, so
no agent can write anything: it can only propose a `PendingAction`.

Reads are scoped too: Jira results outside the configured projects and calendars that
aren't in the config are never returned. Everything a tool returns is untrusted data
(ADR-0021): summaries and titles go to models inside source tags, never as instructions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JiraIssue(_Model):
    key: str
    summary: str
    status: str
    labels: tuple[str, ...] = ()
    priority: str | None = None


class JiraTransition(_Model):
    id: str
    to_status: str


class CalendarEvent(_Model):
    id: str
    title: str
    start: datetime | None  # None for an all-day or unparseable event
    end: datetime | None


class JiraReads(Protocol):
    async def search(self, jql: str, *, limit: int = 20) -> list[JiraIssue]: ...

    async def get_issue(self, key: str) -> JiraIssue: ...

    async def transitions(self, key: str) -> list[JiraTransition]: ...


class JiraWrites(Protocol):
    async def transition(self, key: str, transition_id: str) -> None: ...

    async def update(
        self, key: str, *, labels: list[str] | None = None, priority: str | None = None
    ) -> None: ...


class CalendarReads(Protocol):
    async def list_events(
        self, calendar: str, start: datetime, end: datetime
    ) -> list[CalendarEvent]: ...


class CalendarWrites(Protocol):
    async def create_event(
        self,
        calendar: str,
        *,
        title: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
    ) -> str:
        """Create the event; return its id."""
        ...
