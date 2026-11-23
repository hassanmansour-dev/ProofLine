"""C5 MCP adapters against the demo MCP servers (ADR-0022): the real MCP client path,
in-process, over stdio and over streamable HTTP. No network beyond 127.0.0.1."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from measured_ops.tools.config import (
    OpMap,
    ServerConfig,
    ToolsConfig,
    load_tools_config,
    tools_config_from_env,
)
from measured_ops.tools.demo_servers import (
    STATE_ENV,
    DemoState,
    calendar_server,
    jira_server,
)
from measured_ops.tools.errors import (
    ScopeError,
    ToolConfigError,
    ToolError,
    ToolNotAllowedError,
    ToolUnavailableError,
)
from measured_ops.tools.mcp import (
    McpToolClient,
    build_args,
    build_executors,
    build_read_tools,
    get_path,
)
from measured_ops.tools.specs import CALENDAR_BLOCK, TRIAGE, BlockParams, TriageParams

pytestmark = pytest.mark.anyio


@pytest.fixture
def state(tmp_path: Path) -> DemoState:
    return DemoState(tmp_path / "demo_tools.json")


@pytest.fixture
def servers(state: DemoState) -> dict[str, Any]:
    return {"jira": jira_server(state), "calendar": calendar_server(state)}


CONFIG = load_tools_config()


def test_demo_config_loads_and_splits_reads_from_writes() -> None:
    assert CONFIG.read_tools("jira") == {"search_issues", "get_issue", "get_transitions"}
    assert CONFIG.write_tools("jira") == {"transition_issue", "update_issue"}
    assert CONFIG.read_tools("calendar") == {"list_events"}
    assert CONFIG.write_tools("calendar") == {"create_event"}
    assert CONFIG.scope().jira_projects == {"DEMO"}
    assert CONFIG.scope().calendars == {"personal"}


def _raw(**over: Any) -> dict[str, Any]:
    import tomllib
    from importlib.resources import files

    raw = tomllib.loads(
        files("measured_ops").joinpath("config", "tools.demo.toml").read_text("utf-8")
    )
    for dotted, value in over.items():
        node = raw
        *parents, leaf = dotted.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return raw


@pytest.mark.parametrize(
    ("over", "message"),
    [
        # a read tool that's also a write: an agent's read client could then write
        ({"jira__tools__get_issue__tool": "update_issue"}, "both read and write"),
        ({"jira__server": "nowhere"}, "unknown server"),
        ({"jira__projects": ["demo"]}, "project keys"),
        ({"jira__tools__search__args": {"jql": "jql"}}, "args maps exactly"),
        ({"calendar__calendars": {"Work Cal": "x"}}, "snake_case"),
        ({"servers__jira__env_from": ["lower"]}, "UPPER_SNAKE_CASE"),
        ({"servers__jira__url": "http://example.com/mcp"}, "no url"),
    ],
)
def test_bad_configs_are_refused(over: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ToolsConfig.model_validate(_raw(**over))


def test_http_servers_need_https_and_take_secrets_only_from_env() -> None:
    with pytest.raises(ValueError, match="https"):
        ServerConfig(transport="http", url="http://example.com/mcp")
    ServerConfig(transport="http", url="http://127.0.0.1:9000/mcp")
    ok = ServerConfig(
        transport="http", url="https://mcp.example.com/mcp", headers_from_env={"Authorization": "X"}
    )
    assert ok.headers_from_env == {"Authorization": "X"}


def test_unusable_config_file_is_a_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "tools.toml"
    bad.write_text("version = 2\n")
    with pytest.raises(ToolConfigError, match=str(bad)):
        load_tools_config(bad)
    assert tools_config_from_env({}) is None
    with pytest.raises(ToolConfigError):
        tools_config_from_env({"MEASURED_OPS_TOOLS_CONFIG": str(tmp_path / "missing.toml")})


def test_build_args_nests_dotted_names_and_skips_none() -> None:
    op = OpMap(
        tool="edit",
        args={"key": "issueIdOrKey", "labels": "fields.labels", "priority": "fields.priority.name"},
        const={"cloudId": "site"},
    )
    assert build_args(op, {"key": "DEMO-1", "labels": ["a"], "priority": None}) == {
        "cloudId": "site",
        "issueIdOrKey": "DEMO-1",
        "fields": {"labels": ["a"]},
    }
    assert get_path({"a": {"b": 1}}, "a.b", what="x") == 1
    with pytest.raises(ToolError, match=r"no 'a\.c'"):
        get_path({"a": {"b": 1}}, "a.c", what="x")


# ---- reads -------------------------------------------------------------------------------


async def test_agents_read_tools_refuse_every_write(servers: dict[str, Any]) -> None:
    reads = build_read_tools(CONFIG, servers=servers)
    assert reads.jira is not None
    assert reads.calendar is not None
    for tool in CONFIG.write_tools("jira"):
        with pytest.raises(ToolNotAllowedError):
            await reads.jira.client.call(tool, {})
    with pytest.raises(ToolNotAllowedError):
        await reads.calendar.client.call("create_event", {})
    assert not hasattr(reads.jira, "transition")
    assert not hasattr(reads.calendar, "create_event")


async def test_jira_reads_over_mcp_are_scoped(servers: dict[str, Any], state: DemoState) -> None:
    data = state.load()
    data["jira"]["issues"]["OTHER-1"] = {"summary": "Not ours", "status": "To Do", "labels": []}
    state.save(data)
    jira = build_read_tools(CONFIG, servers=servers).jira
    assert jira is not None

    todo = await jira.search('status = "To Do"')
    # escaping the JQL scope doesn't help: results outside the projects are dropped
    escaped = await jira.search("x) OR (project in (OTHER)")

    assert [i.key for i in todo] == ["DEMO-1", "DEMO-3"]
    assert all(i.key.startswith("DEMO-") for i in escaped)
    issue = await jira.get_issue("DEMO-2")
    assert (issue.status, issue.labels, issue.priority) == ("In Progress", ("inbox",), "Medium")
    assert {t.to_status for t in await jira.transitions("DEMO-2")} == {"To Do", "In Review", "Done"}
    with pytest.raises(ScopeError):
        await jira.get_issue("OTHER-1")


async def test_a_tool_error_result_is_a_tool_error(servers: dict[str, Any]) -> None:
    jira = build_read_tools(CONFIG, servers=servers).jira
    assert jira is not None
    with pytest.raises(ToolError, match=r"jira\.get_issue returned an error: .*DEMO-99"):
        await jira.get_issue("DEMO-99")


async def test_server_error_text_is_pii_redacted(servers: dict[str, Any]) -> None:
    cal = build_read_tools(CONFIG, servers=servers).calendar
    assert cal is not None
    client = McpToolClient("calendar", servers["calendar"], frozenset({"list_events"}))
    with pytest.raises(ToolError) as info:
        await client.call(
            "list_events", {"calendar_id": "someone@example.com", "time_min": "x", "time_max": "y"}
        )
    assert "someone@example.com" not in str(info.value)


# ---- writes through the executors --------------------------------------------------------


async def test_executors_apply_a_move_and_a_block_over_mcp(
    servers: dict[str, Any], state: DemoState
) -> None:
    executors = build_executors(CONFIG, servers=servers)
    move = TRIAGE.propose(
        "t",
        TriageParams(issue="DEMO-1", to_status="In Progress", add_labels=("w48",), reason="Up."),
    )
    start = datetime(2026, 11, 24, 14, tzinfo=UTC)
    block = CALENDAR_BLOCK.propose(
        "t",
        BlockParams(
            calendar="personal", title="Deep work", start=start, end=start + timedelta(hours=1)
        ),
    )

    moved = await executors.run(move, [])
    added = await executors.run(block, [])
    again = await executors.run(block.model_copy(update={"id": "other"}), [])  # fresh run

    data = state.load()
    assert moved.status == "applied"
    assert data["jira"]["issues"]["DEMO-1"]["status"] == "In Progress"
    assert data["jira"]["issues"]["DEMO-1"]["labels"] == ["w48"]
    assert added.status == "applied"
    assert again.status == "already_applied"
    assert again.ref == added.ref
    assert [e["summary"] for e in data["calendar"]["events"]["demo-personal"]] == ["Deep work"]


# ---- transports ---------------------------------------------------------------------------


async def test_stdio_transport_with_the_demo_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STATE_ENV, str(tmp_path / "state.json"))
    jira = build_read_tools(CONFIG).jira  # spawns `python -m measured_ops.tools.demo_servers`
    assert jira is not None
    issue = await jira.get_issue("DEMO-1")
    assert issue.status == "To Do"
    assert (tmp_path / "state.json").exists()  # the env var reached the server, nothing else


async def test_a_missing_env_var_is_a_config_error_before_anything_runs() -> None:
    client = McpToolClient(
        "jira", CONFIG.servers["jira"], frozenset({"get_issue"}), env={"PATH": "/usr/bin"}
    )
    with pytest.raises(ToolConfigError, match="needs MEASURED_OPS_DEMO_TOOLS_STATE"):
        await client.call("get_issue", {"key": "DEMO-1"})


async def test_a_missing_command_is_a_config_error() -> None:
    server = ServerConfig(transport="stdio", command="/nonexistent/mcp-server")
    with pytest.raises(ToolConfigError, match="command wasn't found"):
        await McpToolClient("x", server, frozenset({"t"}), env={}).call("t", {})


async def test_an_unreachable_server_is_transient() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # nothing listens here once closed
    server = ServerConfig(transport="http", url=f"http://127.0.0.1:{port}/mcp", timeout_s=5)
    with pytest.raises(ToolUnavailableError, match="unreachable"):
        await McpToolClient("x", server, frozenset({"t"}), env={}).call("t", {})


@pytest.fixture
def http_jira(state: DemoState) -> Iterator[str]:
    """The demo tracker over streamable HTTP on 127.0.0.1, behind a bearer token."""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse

    app = jira_server(state).streamable_http_app()

    async def require_token(request: Any, call_next: Any) -> Any:
        if request.headers.get("authorization") == "Bearer down":
            return PlainTextResponse("down", status_code=503)
        if request.headers.get("authorization") != "Bearer demo-token":
            return PlainTextResponse("no", status_code=401)
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=require_token)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(5)


async def test_http_transport_sends_the_header_from_env(http_jira: str) -> None:
    server = ServerConfig(
        transport="http", url=http_jira, headers_from_env={"Authorization": "DEMO_JIRA_AUTH"}
    )
    ok = McpToolClient(
        "jira", server, frozenset({"get_issue"}), env={"DEMO_JIRA_AUTH": "Bearer demo-token"}
    )
    assert (await ok.call("get_issue", {"key": "DEMO-2"}))["status"] == "In Progress"

    wrong = McpToolClient(
        "jira", server, frozenset({"get_issue"}), env={"DEMO_JIRA_AUTH": "Bearer x"}
    )
    with pytest.raises(ToolError, match="HTTP 401") as info:
        await wrong.call("get_issue", {"key": "DEMO-2"})
    assert not isinstance(info.value, ToolUnavailableError)  # auth isn't retried

    down = McpToolClient(
        "jira", server, frozenset({"get_issue"}), env={"DEMO_JIRA_AUTH": "Bearer down"}
    )
    with pytest.raises(ToolUnavailableError, match="HTTP 503"):  # retried by the execute node
        await down.call("get_issue", {"key": "DEMO-2"})
