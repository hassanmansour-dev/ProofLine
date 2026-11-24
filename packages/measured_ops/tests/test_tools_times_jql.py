"""Event times as real calendar servers write them, and JQL scoping with ORDER BY.

The time shapes are the ones the chosen Outlook calendar server returns (text with seven
fractional digits and a zone label) and Microsoft Graph's dateTime + timeZone pair."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from measured_ops.tools.config import load_tools_config
from measured_ops.tools.errors import ToolError
from measured_ops.tools.executors import CalendarBlockExecutor
from measured_ops.tools.mcp import McpCalendarReads, McpJiraReads, parse_event_time, scope_jql
from measured_ops.tools.specs import BlockParams

pytestmark = pytest.mark.anyio
TWO_PM = datetime(2026, 12, 1, 14, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "2026-12-01T14:00:00.0000000 (UTC)",  # outlook_list_events, exactly
        "2026-12-01T14:00:00.0000000",  # with the zone given separately, below
        "2026-12-01T14:00:00Z",
        "2026-12-01T14:00:00.0000000Z",
        "2026-12-01T14:00:00+00:00",
        "2026-12-01T09:00:00-05:00",
        "2026-12-01T09:00:00.0000000 (America/Toronto)",
        "2026-12-01 14:00:00 UTC",
        {"dateTime": "2026-12-01T14:00:00.0000000", "timeZone": "UTC"},  # Microsoft Graph
        {"dateTime": "2026-12-01T09:00:00.0000000", "timeZone": "America/Toronto"},
        {"dateTime": "2026-12-01T09:00:00.0000000", "timeZone": "Eastern Standard Time"},
        {"dateTime": "2026-12-01T14:00:00Z"},
    ],
)
def test_event_times_are_read_as_the_same_instant(value: Any) -> None:
    zone = "UTC" if value == "2026-12-01T14:00:00.0000000" else None
    assert parse_event_time(value, default_zone=zone) == TWO_PM


def test_all_day_and_missing_times_have_no_instant() -> None:
    assert parse_event_time("2026-12-01") is None  # an all-day event
    assert parse_event_time({"date": "2026-12-01"}) is None
    assert parse_event_time(None) is None


@pytest.mark.parametrize(
    "value",
    [
        "tomorrow at two",
        "2026-12-01T14:00:00",  # no zone anywhere: which instant is it?
        "2026-12-01T14:00:00.0000000 (Mars Standard Time)",
        {"dateTime": "2026-12-01T14:00:00", "timeZone": "Not/AZone"},
        {"start": "2026-12-01T14:00:00Z"},
        14,
    ],
)
def test_a_time_it_cannot_read_fails_loudly(value: Any) -> None:
    # Never "no instant": that would make the block look absent and create a duplicate.
    with pytest.raises(ToolError, match="event time"):
        parse_event_time(value)


class _Client:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.created: list[dict[str, Any]] = []

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        if tool == "create_event":
            self.created.append(args)
            return {"id": "new"}
        return {"events": self.events}


async def test_a_block_already_on_an_outlook_calendar_is_not_created_twice() -> None:
    config = load_tools_config().calendar
    assert config is not None
    client = _Client(
        [
            {
                "id": "evt-1",
                "summary": "Deep work",
                "start": "2026-12-01T14:00:00.0000000 (UTC)",
                "end": "2026-12-01T15:00:00.0000000 (UTC)",
            }
        ]
    )
    reads = McpCalendarReads(client, config)  # type: ignore[arg-type]
    block = BlockParams(
        calendar="personal",
        title="Deep work",
        start=datetime(2026, 12, 1, 9, 0, tzinfo=timezone(timedelta(hours=-5))),
        end=datetime(2026, 12, 1, 10, 0, tzinfo=timezone(timedelta(hours=-5))),
    )

    class _NoWrites:
        async def create_event(self, *a: Any, **k: Any) -> str:
            raise AssertionError("a duplicate block would be created")

    result = await CalendarBlockExecutor(reads, _NoWrites()).run(block)

    assert (result.status, result.ref) == ("already_applied", "evt-1")


async def test_an_unreadable_event_time_stops_the_executor() -> None:
    config = load_tools_config().calendar
    assert config is not None
    client = _Client([{"id": "e", "summary": "Deep work", "start": "soon", "end": "later"}])
    reads = McpCalendarReads(client, config)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="event time"):
        await reads.list_events("personal", TWO_PM, TWO_PM + timedelta(hours=1))


# ---- JQL ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("jql", "scoped"),
    [
        ('status = "To Do"', 'project in (GROW) AND (status = "To Do")'),
        (
            'status = "To Do" ORDER BY created DESC',
            'project in (GROW) AND (status = "To Do") ORDER BY created DESC',
        ),
        (
            "assignee = currentUser() order by priority DESC, updated ASC",
            "project in (GROW) AND (assignee = currentUser()) order by priority DESC, updated ASC",
        ),
        ("ORDER BY rank", "project in (GROW) ORDER BY rank"),
        ("", "project in (GROW)"),
        (
            'summary ~ "sort order by hand" ORDER BY created',
            'project in (GROW) AND (summary ~ "sort order by hand") ORDER BY created',
        ),
        ('summary ~ "order by"', 'project in (GROW) AND (summary ~ "order by")'),
    ],
)
def test_the_project_scope_keeps_order_by_outside_the_parentheses(jql: str, scoped: str) -> None:
    assert scope_jql(jql, ("GROW",)) == scoped


async def test_search_sends_the_scoped_query() -> None:
    config = load_tools_config().jira
    assert config is not None
    sent: list[dict[str, Any]] = []

    class _Jira:
        async def call(self, tool: str, args: dict[str, Any]) -> Any:
            sent.append(args)
            return {"issues": []}

    await McpJiraReads(_Jira(), config).search('status = "To Do" ORDER BY created DESC')  # type: ignore[arg-type]
    assert sent[0]["jql"] == 'project in (DEMO) AND (status = "To Do") ORDER BY created DESC'
