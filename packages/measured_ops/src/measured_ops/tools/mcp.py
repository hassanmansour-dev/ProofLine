"""MCP adapters for the tool ports (C5, ADR-0022). Needs the `tools` extra (`mcp`).

- `McpToolClient`: one MCP server, one allowlist of tool names. Each call opens a session,
  calls one tool and closes it: no session to go stale in a long-running inbox, at the
  cost of a handshake per call (fine at a few calls per action). A tool outside the
  allowlist raises `ToolNotAllowedError` before anything is sent.
- The read/write split is two clients per server: `build_read_tools` gives agents clients
  whose allowlist is the config's read tools only; `build_executors` gives the executors a
  read client and a write client. The config refuses a tool listed as both.
- Errors: unreachable, timed out, connection closed, HTTP 5xx → `ToolUnavailableError`
  (the execute node retries); an error result, auth failure, non-JSON or unexpected
  result → `ToolError`; a missing command or env var → `ToolConfigError`. Messages never
  carry values read from a record; a server's own error text is PII-redacted and cut short.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2
import mcp_types
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from measured_evals.checks import redact_pii
from measured_ops.tools.config import (
    PYTHON,
    CalendarConfig,
    JiraConfig,
    OpMap,
    ServerConfig,
    ToolsConfig,
    tools_config_from_env,
)
from measured_ops.tools.errors import (
    ScopeError,
    ToolConfigError,
    ToolError,
    ToolNotAllowedError,
    ToolUnavailableError,
)
from measured_ops.tools.executors import CalendarBlockExecutor, Executors, TriageExecutor
from measured_ops.tools.ports import CalendarEvent, JiraIssue, JiraTransition

log = logging.getLogger("measured_ops.tools")

MAX_SEARCH = 50
DEADLINE_FACTOR = 2  # a whole call takes at most twice the server's timeout_s
ERROR_TEXT_MAX = 200
Target = ServerConfig | MCPServer  # an MCPServer instance: in-process, for tests and demos


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaves(inner)]
    return [exc]


def _classify(
    server: str, tool: str, exc: BaseException, http_status: int | None = None
) -> ToolError:
    """Map what the MCP client raised to the tool layer's errors. `http_status`: the last
    HTTP error status seen (the SDK reports any 4xx/5xx as one generic MCP error)."""
    where = f"{server}.{tool}"
    if http_status is not None:
        if http_status >= 500 or http_status == 429:
            return ToolUnavailableError(f"{where}: HTTP {http_status}")
        return ToolError(f"{where}: HTTP {http_status} (check the server's credentials)")
    for leaf in _leaves(exc):
        if isinstance(leaf, FileNotFoundError):
            return ToolConfigError(f"{where}: the server's command wasn't found")
        if isinstance(leaf, httpx2.HTTPStatusError):
            code = leaf.response.status_code
            if code >= 500 or code == 429:
                return ToolUnavailableError(f"{where}: HTTP {code}")
            return ToolError(f"{where}: HTTP {code} (check the server's credentials)")
        if isinstance(leaf, TimeoutError | httpx2.TransportError | ConnectionError | OSError):
            return ToolUnavailableError(f"{where}: unreachable ({type(leaf).__name__})")
        if isinstance(leaf, MCPError):
            if "connection closed" in str(leaf).casefold():
                return ToolUnavailableError(f"{where}: the server closed the connection")
            return ToolError(f"{where}: MCP error {leaf.code}")
    return ToolError(f"{where}: failed ({type(exc).__name__})")


def _payload(server: str, tool: str, result: mcp_types.CallToolResult) -> Any:
    if result.structured_content is not None:
        return result.structured_content
    text = "".join(c.text for c in result.content if isinstance(c, mcp_types.TextContent))
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise ToolError(f"{server}.{tool} returned text that isn't JSON") from None


class McpToolClient:
    """One MCP server, restricted to `allowed` tool names."""

    def __init__(
        self,
        name: str,
        target: Target,
        allowed: frozenset[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.target = target
        self.allowed = allowed
        self._env = os.environ if env is None else env

    @property
    def timeout_s(self) -> float:
        return self.target.timeout_s if isinstance(self.target, ServerConfig) else 30.0

    def _from_env(self, var: str) -> str:
        value = self._env.get(var)
        if not value:
            raise ToolConfigError(f"server {self.name!r} needs {var} in the environment")
        return value

    @asynccontextmanager
    async def _connect(self, statuses: list[int]) -> AsyncIterator[Client]:
        """A connected client. HTTP error statuses seen on the way go into `statuses`."""
        t = self.target
        if isinstance(t, MCPServer):
            async with Client(t, read_timeout_seconds=self.timeout_s) as client:
                yield client
        elif t.transport == "stdio":
            assert t.command is not None  # ServerConfig checks it
            params = StdioServerParameters(
                command=sys.executable if t.command == PYTHON else t.command,
                args=list(t.args),
                env={var: self._from_env(var) for var in t.env_from},
                cwd=t.cwd,
            )
            async with Client(params, read_timeout_seconds=t.timeout_s) as client:
                yield client
        else:
            assert t.url is not None
            headers = {h: self._from_env(var) for h, var in t.headers_from_env.items()}

            async def record(response: httpx2.Response) -> None:
                if response.status_code >= 400:
                    statuses.append(response.status_code)

            async with (
                httpx2.AsyncClient(
                    headers=headers, timeout=t.timeout_s, event_hooks={"response": [record]}
                ) as http,
                Client(
                    streamable_http_client(t.url, http_client=http),
                    read_timeout_seconds=t.timeout_s,
                ) as client,
            ):
                yield client

    async def call(self, tool: str, args: Mapping[str, Any]) -> Any:
        """Call `tool`; return its JSON result (None when it returns no text)."""
        if tool not in self.allowed:
            raise ToolNotAllowedError(f"{self.name}.{tool} is not allowed on this client")
        statuses: list[int] = []
        try:
            # One deadline for the whole call (start, handshake, call), on top of the
            # per-request timeout: a server that hangs before answering can't hold a run.
            async with asyncio.timeout(DEADLINE_FACTOR * self.timeout_s):
                async with self._connect(statuses) as client:
                    result = await client.call_tool(tool, dict(args))
        except ToolError:
            raise
        except Exception as exc:
            status = statuses[-1] if statuses else None
            raise _classify(self.name, tool, exc, status) from exc
        if result.is_error:
            text = "".join(c.text for c in result.content if isinstance(c, mcp_types.TextContent))
            said = redact_pii(text.strip())[:ERROR_TEXT_MAX] or "no details"
            raise ToolError(f"{self.name}.{tool} returned an error: {said}")
        return _payload(self.name, tool, result)

    async def list_tools(self) -> list[mcp_types.Tool]:
        """The server's tool list (names and input schemas), for `tools check`."""
        statuses: list[int] = []
        try:
            async with asyncio.timeout(DEADLINE_FACTOR * self.timeout_s):
                async with self._connect(statuses) as client:
                    listing = await client.list_tools()
        except ToolError:
            raise
        except Exception as exc:
            status = statuses[-1] if statuses else None
            raise _classify(self.name, "tools/list", exc, status) from exc
        return list(listing.tools)


# ---- mapping logical operations onto a server's tools ---------------------------------


def get_path(obj: Any, path: str | None, *, what: str) -> Any:
    """`obj` at a dotted path (None = obj itself). Raises ToolError naming the path."""
    if path is None:
        return obj
    for part in path.split("."):
        if not isinstance(obj, Mapping) or part not in obj:
            raise ToolError(f"{what}: no {path!r} in the result")
        obj = obj[part]
    return obj


def build_args(op: OpMap, values: Mapping[str, Any]) -> dict[str, Any]:
    """The tool's arguments: `const`, then each logical value (None skipped) at its
    mapped, possibly dotted, name."""
    out: dict[str, Any] = json.loads(json.dumps(op.const))  # a deep copy
    for logical, value in values.items():
        if value is None:
            continue
        *parents, leaf = op.args[logical].split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ToolConfigError(f"{op.tool}: argument {op.args[logical]!r} clashes")
        node[leaf] = value
    return out


def _str(value: Any, what: str) -> str:
    if not isinstance(value, str | int) or isinstance(value, bool):
        raise ToolError(f"{what} is not text")
    return str(value)


# Windows zone names Microsoft Graph uses, for the zones this is likely to meet. Anything
# else must be an IANA name or UTC; an unknown name fails loudly (configure the server to
# answer in UTC, or add the name here).
WINDOWS_ZONES = {
    "utc": "UTC",
    "gmt": "UTC",
    "z": "UTC",
    "coordinated universal time": "UTC",
    "eastern standard time": "America/Toronto",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "atlantic standard time": "America/Halifax",
    "newfoundland standard time": "America/St_Johns",
    "gmt standard time": "Europe/London",
    "w. europe standard time": "Europe/Berlin",
    "romance standard time": "Europe/Paris",
}
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LABEL = re.compile(
    r"^(?P<stamp>.*?\d)\s*(?:\((?P<paren>[^()]+)\)|\s(?P<bare>[A-Za-z][A-Za-z_/+\-. ]*))$"
)
_FRACTION = re.compile(r"(\.\d{6})\d+")


def _zone(name: str) -> tzinfo:
    key = name.strip()
    try:
        return ZoneInfo(WINDOWS_ZONES.get(key.casefold(), key))
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError("event time: unknown time zone label in a tool result") from None


def parse_event_time(value: Any, *, default_zone: str | None = None) -> datetime | None:
    """An event's start or end as an aware datetime, or None when it has no instant (an
    all-day date, or no value). Reads what calendar servers actually send:

    - ISO 8601 with an offset or `Z`, with any number of fractional digits;
    - the same followed by a zone label: `2026-12-01T14:00:00.0000000 (UTC)`, `... UTC`;
    - Microsoft Graph's pair `{"dateTime": "...", "timeZone": "..."}` (IANA, UTC or a
      common Windows zone name);
    - a time with no zone at all only when the config names one (`default_zone`).

    Anything else raises ToolError. It never returns None for a value it couldn't read:
    "no instant" would make an existing block look absent, and a retry would duplicate it.
    The message names the problem, never the value (titles and times are personal)."""
    if value is None:
        return None
    zone_name = default_zone
    if isinstance(value, Mapping):
        if "dateTime" not in value:
            if isinstance(value.get("date"), str) and _DATE_ONLY.match(value["date"]):
                return None  # an all-day event
            raise ToolError("event time: an object without dateTime in a tool result")
        zone_name = value.get("timeZone") or default_zone
        value = value["dateTime"]
    if not isinstance(value, str):
        raise ToolError("event time: not text in a tool result")
    text = value.strip()
    if _DATE_ONLY.match(text):
        return None  # an all-day event
    labelled = _LABEL.match(text)
    if labelled and (labelled["paren"] or labelled["bare"]):
        text, zone_name = labelled["stamp"], labelled["paren"] or labelled["bare"]
    text = _FRACTION.sub(r"\1", text.replace(" ", "T", 1) if "T" not in text else text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ToolError("event time: unreadable date and time in a tool result") from None
    if parsed.tzinfo is not None:
        return parsed
    if not zone_name:
        raise ToolError(
            "event time: no time zone in a tool result; set calendar.fields.time_zone "
            "in the tools config, or have the server answer in UTC"
        )
    return parsed.replace(tzinfo=_zone(str(zone_name)))


_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)


def scope_jql(jql: str, projects: Sequence[str]) -> str:
    """`jql` limited to `projects`. An ORDER BY clause stays outside the parentheses
    (`project in (X) AND (cond) ORDER BY ...`): inside them it's a syntax error. Quoted
    text is skipped when looking for it."""
    scope = f"project in ({', '.join(projects)})"
    cut, quote = None, None
    for i, ch in enumerate(jql):
        if quote:
            if ch == quote and jql[i - 1] != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "oO" and _ORDER_BY.match(jql, i) and (i == 0 or not jql[i - 1].isalnum()):
            cut = i  # the last top-level one wins
    condition, order = (jql, "") if cut is None else (jql[:cut], jql[cut:])
    condition, order = condition.strip(), order.strip()
    scoped = f"{scope} AND ({condition})" if condition else scope
    return f"{scoped} {order}" if order else scoped


class McpJiraReads:
    """`JiraReads` over MCP, limited to the configured projects."""

    def __init__(self, client: McpToolClient, config: JiraConfig) -> None:
        self.client = client
        self.cfg = config

    def _in_scope(self, key: str) -> bool:
        return key.split("-", 1)[0] in self.cfg.projects

    def _issue(self, raw: Any) -> JiraIssue:
        f = self.cfg.fields
        what = f"{self.cfg.server} issue"
        labels = get_path(raw, f.labels, what=what) if _has(raw, f.labels) else []
        priority = get_path(raw, f.priority, what=what) if _has(raw, f.priority) else None
        try:
            return JiraIssue(
                key=_str(get_path(raw, f.key, what=what), "issue key"),
                summary=_str(get_path(raw, f.summary, what=what), "issue summary"),
                status=_str(get_path(raw, f.status, what=what), "issue status"),
                labels=tuple(_str(x, "label") for x in labels or []),
                priority=None if priority is None else _str(priority, "priority"),
            )
        except ValidationError as exc:
            raise ToolError(f"{what}: unexpected shape") from exc

    async def search(self, jql: str, *, limit: int = 20) -> list[JiraIssue]:
        op = self.cfg.tools.search
        scoped = scope_jql(jql, self.cfg.projects)
        limit = max(1, min(limit, MAX_SEARCH))
        raw = await self.client.call(op.tool, build_args(op, {"jql": scoped, "limit": limit}))
        items = get_path(raw, op.items, what=f"{self.cfg.server}.{op.tool}")
        if not isinstance(items, list):
            raise ToolError(f"{self.cfg.server}.{op.tool}: {op.items!r} is not a list")
        issues = [self._issue(item) for item in items]
        # The JQL scope is a courtesy; this filter is the guard (JQL can be escaped).
        return [i for i in issues if self._in_scope(i.key)][:limit]

    async def get_issue(self, key: str) -> JiraIssue:
        if not self._in_scope(key):
            raise ScopeError(f"{key} is not in a configured Jira project")
        op = self.cfg.tools.get_issue
        raw = await self.client.call(op.tool, build_args(op, {"key": key}))
        return self._issue(get_path(raw, op.result, what=f"{self.cfg.server}.{op.tool}"))

    async def transitions(self, key: str) -> list[JiraTransition]:
        if not self._in_scope(key):
            raise ScopeError(f"{key} is not in a configured Jira project")
        op = self.cfg.tools.transitions
        raw = await self.client.call(op.tool, build_args(op, {"key": key}))
        items = get_path(raw, op.items, what=f"{self.cfg.server}.{op.tool}")
        if not isinstance(items, list):
            raise ToolError(f"{self.cfg.server}.{op.tool}: {op.items!r} is not a list")
        tf = self.cfg.transition_fields
        what = f"{self.cfg.server} transition"
        return [
            JiraTransition(
                id=_str(get_path(t, tf.id, what=what), "transition id"),
                to_status=_str(get_path(t, tf.to_status, what=what), "transition status"),
            )
            for t in items
        ]


def _has(obj: Any, path: str) -> bool:
    try:
        get_path(obj, path, what="")
    except ToolError:
        return False
    return True


class McpJiraWrites:
    """`JiraWrites` over MCP. Only the executors hold one."""

    def __init__(self, client: McpToolClient, config: JiraConfig) -> None:
        self.client = client
        self.cfg = config

    def _check(self, key: str) -> None:
        if key.split("-", 1)[0] not in self.cfg.projects:
            raise ScopeError(f"{key} is not in a configured Jira project")

    async def transition(self, key: str, transition_id: str) -> None:
        self._check(key)
        op = self.cfg.tools.transition
        await self.client.call(
            op.tool, build_args(op, {"key": key, "transition_id": transition_id})
        )

    async def update(
        self, key: str, *, labels: list[str] | None = None, priority: str | None = None
    ) -> None:
        self._check(key)
        op = self.cfg.tools.update
        values = {"key": key, "labels": labels, "priority": priority}
        await self.client.call(op.tool, build_args(op, values))


def _calendar_id(cfg: CalendarConfig, alias: str) -> str:
    if alias not in cfg.calendars:
        raise ScopeError(f"calendar {alias!r} is not in the config")
    return cfg.calendars[alias]


class McpCalendarReads:
    """`CalendarReads` over MCP, for the configured (personal) calendars only."""

    def __init__(self, client: McpToolClient, config: CalendarConfig) -> None:
        self.client = client
        self.cfg = config

    async def list_events(
        self, calendar: str, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        op = self.cfg.tools.list_events
        values = {
            "calendar_id": _calendar_id(self.cfg, calendar),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        raw = await self.client.call(op.tool, build_args(op, values))
        items = get_path(raw, op.items, what=f"{self.cfg.server}.{op.tool}")
        if not isinstance(items, list):
            raise ToolError(f"{self.cfg.server}.{op.tool}: {op.items!r} is not a list")
        f = self.cfg.fields
        what = f"{self.cfg.server} event"
        return [
            CalendarEvent(
                id=_str(get_path(e, f.id, what=what), "event id"),
                title=_str(get_path(e, f.title, what=what), "event title")
                if _has(e, f.title)
                else "",
                start=parse_event_time(get_path(e, f.start, what=what), default_zone=f.time_zone)
                if _has(e, f.start)
                else None,
                end=parse_event_time(get_path(e, f.end, what=what), default_zone=f.time_zone)
                if _has(e, f.end)
                else None,
            )
            for e in items
        ]


class McpCalendarWrites:
    """`CalendarWrites` over MCP. Only the executors hold one."""

    def __init__(self, client: McpToolClient, config: CalendarConfig) -> None:
        self.client = client
        self.cfg = config

    async def create_event(
        self,
        calendar: str,
        *,
        title: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
    ) -> str:
        op = self.cfg.tools.create_event
        values = {
            "calendar_id": _calendar_id(self.cfg, calendar),
            "title": title,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "description": description,
        }
        raw = await self.client.call(op.tool, build_args(op, values))
        return _str(get_path(raw, op.id, what=f"{self.cfg.server}.{op.tool}"), "new event id")


# ---- builders ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadTools:
    """What an agent gets: read ports only. None where the config has no such section."""

    jira: McpJiraReads | None = None
    calendar: McpCalendarReads | None = None


def _target(config: ToolsConfig, server: str, overrides: Mapping[str, MCPServer] | None) -> Target:
    if overrides and server in overrides:
        return overrides[server]
    return config.servers[server]


def build_read_tools(
    config: ToolsConfig,
    *,
    servers: Mapping[str, MCPServer] | None = None,
    env: Mapping[str, str] | None = None,
) -> ReadTools:
    """Read-only ports for agents. `servers` swaps a configured server for an in-process
    one (tests, demos)."""

    def reader(server: str) -> McpToolClient:
        return McpToolClient(
            server, _target(config, server, servers), config.read_tools(server), env=env
        )

    return ReadTools(
        jira=McpJiraReads(reader(config.jira.server), config.jira) if config.jira else None,
        calendar=McpCalendarReads(reader(config.calendar.server), config.calendar)
        if config.calendar
        else None,
    )


def build_executors(
    config: ToolsConfig,
    *,
    servers: Mapping[str, MCPServer] | None = None,
    env: Mapping[str, str] | None = None,
) -> Executors:
    """The executors for the config's sections, each with a read and a write client."""

    def client(server: str, allowed: frozenset[str]) -> McpToolClient:
        return McpToolClient(server, _target(config, server, servers), allowed, env=env)

    executors: list[TriageExecutor | CalendarBlockExecutor] = []
    if config.jira:
        s = config.jira.server
        executors.append(
            TriageExecutor(
                McpJiraReads(client(s, config.read_tools(s)), config.jira),
                McpJiraWrites(client(s, config.write_tools(s)), config.jira),
            )
        )
    if config.calendar:
        s = config.calendar.server
        executors.append(
            CalendarBlockExecutor(
                McpCalendarReads(client(s, config.read_tools(s)), config.calendar),
                McpCalendarWrites(client(s, config.write_tools(s)), config.calendar),
            )
        )
    return Executors(executors, config.scope())


def executors_from_env() -> Executors | None:
    """Executors for `MEASURED_OPS_TOOLS_CONFIG`, or None when it's unset."""
    config = tools_config_from_env()
    return None if config is None else build_executors(config)
