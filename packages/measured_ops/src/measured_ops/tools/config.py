"""Tools config (C5, ADR-0022): which MCP servers to reach, and how each logical
operation maps onto one of their tools. TOML, like the approval policy.

`load_tools_config()` reads the public demo config bundled with this package (it points
at the demo servers in `measured_ops.tools.demo_servers`). The real one lives in
measured-ops-private and is passed by path, or named by `MEASURED_OPS_TOOLS_CONFIG`.

No secret is ever written in a config: a server gets credentials only from environment
variables the config **names** (`env_from` for a stdio server, `headers_from_env` for an
HTTP one). Calendar ids are config too: actions and logs use aliases (`personal`), never ids.

Every MCP server names its tools and arguments its own way, so each operation says which
tool to call, how the logical arguments are named there (dotted names build nested
objects: `labels = "fields.labels"`), constant arguments (e.g. a site id) and where the
answer is in the result (dotted paths). `python -m measured_ops.tools check` verifies a
config against the servers' own tool lists before anything runs.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from measured_ops.contracts import NAME_PATTERN
from measured_ops.tools.errors import ToolConfigError
from measured_ops.tools.specs import ToolScope

DEMO_CONFIG = "tools.demo.toml"
ENV_CONFIG = "MEASURED_OPS_TOOLS_CONFIG"
PYTHON = "{python}"  # in `command`: the interpreter running Measured Ops
TOOL_NAME = r"^[A-Za-z0-9_.\-/]{1,128}$"
ENV_NAME = r"^[A-Z_][A-Z0-9_]{0,63}$"
PROJECT_KEY = r"^[A-Z][A-Z0-9_]{1,9}$"
PATH = r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerConfig(_Model):
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: tuple[str, ...] = ()
    env_from: tuple[str, ...] = Field(default=(), description="Env var names passed through.")
    cwd: str | None = None
    url: str | None = None
    headers_from_env: dict[str, str] = Field(
        default_factory=dict, description="HTTP header name → env var holding its value."
    )
    timeout_s: float = Field(default=30.0, gt=0, le=300)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.transport == "stdio":
            if not self.command or self.url or self.headers_from_env:
                raise ValueError("a stdio server has a command, and no url or headers")
        else:
            if not self.url or self.command or self.args or self.env_from:
                raise ValueError("an http server has a url, and no command, args or env_from")
            local = self.url.startswith(("http://127.0.0.1", "http://localhost"))
            if not (self.url.startswith("https://") or local):
                raise ValueError("an http server's url is https (or http on localhost)")
        names = [*self.env_from, *self.headers_from_env.values()]
        bad = [n for n in names if not re.match(ENV_NAME, n)]
        if bad:
            raise ValueError(f"env var names are UPPER_SNAKE_CASE: {bad}")
        return self


class OpMap(_Model):
    """One logical operation → one MCP tool."""

    tool: str = Field(pattern=TOOL_NAME)
    args: dict[str, str] = Field(default_factory=dict, description="logical → tool argument")
    const: dict[str, JsonValue] = Field(default_factory=dict)
    items: str | None = Field(default=None, pattern=PATH, description="list in the result")
    result: str | None = Field(
        default=None, pattern=PATH, description="the object in the result (unset: the root)"
    )
    id: str | None = Field(default=None, pattern=PATH, description="new id in the result")

    @model_validator(mode="after")
    def _paths(self) -> Self:
        bad = [a for a in self.args.values() if not re.match(PATH, a)]
        if bad:
            raise ValueError(f"argument names are dotted paths: {bad}")
        return self


def _need(op: OpMap, name: str, args: set[str], *, items: bool = False, id_: bool = False) -> None:
    if set(op.args) != args:
        raise ValueError(f"{name}: args maps exactly {sorted(args)}, got {sorted(op.args)}")
    if items and not op.items:
        raise ValueError(f"{name}: set `items`, the path to the list in the result")
    if id_ and not op.id:
        raise ValueError(f"{name}: set `id`, the path to the new id in the result")


class JiraFields(_Model):
    key: str = Field(default="key", pattern=PATH)
    summary: str = Field(default="summary", pattern=PATH)
    status: str = Field(default="status", pattern=PATH)
    labels: str = Field(default="labels", pattern=PATH)
    priority: str = Field(default="priority", pattern=PATH)


class TransitionFields(_Model):
    id: str = Field(default="id", pattern=PATH)
    to_status: str = Field(default="to", pattern=PATH)


class JiraTools(_Model):
    search: OpMap
    get_issue: OpMap
    transitions: OpMap
    transition: OpMap  # write
    update: OpMap  # write

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _need(self.search, "search", {"jql", "limit"}, items=True)
        _need(self.get_issue, "get_issue", {"key"})
        _need(self.transitions, "transitions", {"key"}, items=True)
        _need(self.transition, "transition", {"key", "transition_id"})
        _need(self.update, "update", {"key", "labels", "priority"})
        return self


class JiraConfig(_Model):
    server: str
    projects: tuple[str, ...] = Field(min_length=1)
    tools: JiraTools
    fields: JiraFields = JiraFields()
    transition_fields: TransitionFields = TransitionFields()

    @model_validator(mode="after")
    def _keys(self) -> Self:
        bad = [p for p in self.projects if not re.match(PROJECT_KEY, p)]
        if bad:
            raise ValueError(f"Jira project keys look like GROW: {bad}")
        return self

    @property
    def reads(self) -> tuple[OpMap, ...]:
        return (self.tools.search, self.tools.get_issue, self.tools.transitions)

    @property
    def writes(self) -> tuple[OpMap, ...]:
        return (self.tools.transition, self.tools.update)


class EventFields(_Model):
    id: str = Field(default="id", pattern=PATH)
    title: str = Field(default="title", pattern=PATH)
    start: str = Field(default="start", pattern=PATH)
    end: str = Field(default="end", pattern=PATH)
    time_zone: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Zone for event times the server returns with no zone at all (IANA name or UTC). "
            "Unset: such a time is an error, never a guess."
        ),
    )


class CalendarTools(_Model):
    list_events: OpMap
    create_event: OpMap  # write

    @model_validator(mode="after")
    def _complete(self) -> Self:
        _need(self.list_events, "list_events", {"calendar_id", "start", "end"}, items=True)
        _need(
            self.create_event,
            "create_event",
            {"calendar_id", "title", "start", "end", "description"},
            id_=True,
        )
        return self


class CalendarConfig(_Model):
    server: str
    # alias → calendar id. Personal calendars only: never a work calendar (GROW-84).
    calendars: dict[str, str] = Field(min_length=1)
    tools: CalendarTools
    fields: EventFields = EventFields()

    @model_validator(mode="after")
    def _aliases(self) -> Self:
        bad = [a for a in self.calendars if not re.match(NAME_PATTERN, a)]
        if bad:
            raise ValueError(f"calendar aliases are snake_case: {bad}")
        return self

    @property
    def reads(self) -> tuple[OpMap, ...]:
        return (self.tools.list_events,)

    @property
    def writes(self) -> tuple[OpMap, ...]:
        return (self.tools.create_event,)


class ToolsConfig(_Model):
    version: Literal[1]
    servers: dict[str, ServerConfig] = Field(default_factory=dict)
    jira: JiraConfig | None = None
    calendar: CalendarConfig | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        reads: dict[str, set[str]] = {}
        writes: dict[str, set[str]] = {}
        for section in (self.jira, self.calendar):
            if section is None:
                continue
            if section.server not in self.servers:
                raise ValueError(f"unknown server {section.server!r}")
            reads.setdefault(section.server, set()).update(op.tool for op in section.reads)
            writes.setdefault(section.server, set()).update(op.tool for op in section.writes)
        for server, names in reads.items():
            both = names & writes.get(server, set())
            if both:
                # the read/write split: an agent's read client must never reach a write
                raise ValueError(f"server {server!r}: tools both read and write: {sorted(both)}")
        return self

    def scope(self) -> ToolScope:
        return ToolScope(
            jira_projects=frozenset(self.jira.projects if self.jira else ()),
            calendars=frozenset(self.calendar.calendars if self.calendar else ()),
        )

    def read_tools(self, server: str) -> frozenset[str]:
        return frozenset(
            op.tool
            for s in (self.jira, self.calendar)
            if s is not None and s.server == server
            for op in s.reads
        )

    def write_tools(self, server: str) -> frozenset[str]:
        return frozenset(
            op.tool
            for s in (self.jira, self.calendar)
            if s is not None and s.server == server
            for op in s.writes
        )


def load_tools_config(path: Path | None = None) -> ToolsConfig:
    """Load a tools config. No path = the public demo config bundled with this package."""
    try:
        if path is None:
            raw = files("measured_ops").joinpath("config", DEMO_CONFIG).read_text("utf-8")
        else:
            raw = path.read_text(encoding="utf-8")
        return ToolsConfig.model_validate(tomllib.loads(raw))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        where = "the demo tools config" if path is None else f"tools config {path}"
        raise ToolConfigError(f"{where} is unusable: {exc}") from exc


def tools_config_from_env(env: Mapping[str, str] | None = None) -> ToolsConfig | None:
    """The config `MEASURED_OPS_TOOLS_CONFIG` names, or None when it's unset (no tools)."""
    raw = (os.environ if env is None else env).get(ENV_CONFIG, "").strip()
    return load_tools_config(Path(raw)) if raw else None
