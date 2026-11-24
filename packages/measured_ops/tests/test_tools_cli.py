"""`python -m measured_ops.tools check|list` (C5): a config checked against tool lists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mcp_types
import pytest

from measured_ops.tools.__main__ import check_config, main
from measured_ops.tools.config import ToolsConfig, load_tools_config
from measured_ops.tools.demo_servers import STATE_ENV, DemoState, calendar_server, jira_server
from measured_ops.tools.mcp import McpToolClient

pytestmark = pytest.mark.anyio


async def _listings(tmp_path: Path) -> dict[str, list[mcp_types.Tool]]:
    state = DemoState(tmp_path / "s.json")
    return {
        name: await McpToolClient(name, server, frozenset()).list_tools()
        for name, server in (("jira", jira_server(state)), ("calendar", calendar_server(state)))
    }


async def test_demo_config_matches_the_demo_servers(tmp_path: Path) -> None:
    problems, ok = check_config(load_tools_config(), await _listings(tmp_path))
    assert problems == []
    assert len(ok) == 7
    assert "ok  jira.transition -> jira.transition_issue (write)" in ok


async def test_mismatches_are_reported(tmp_path: Path) -> None:
    raw: dict[str, Any] = load_tools_config().model_dump()
    raw["jira"]["tools"]["get_issue"]["tool"] = "fetch_issue"
    raw["jira"]["tools"]["transition"]["args"]["transition_id"] = "transitionId"
    raw["calendar"]["tools"]["create_event"]["args"]["title"] = "fields.summary"
    config = ToolsConfig.model_validate(raw)

    problems, _ = check_config(config, await _listings(tmp_path))

    assert "jira.get_issue: server 'jira' has no tool 'fetch_issue'" in problems
    assert any("transition_issue has no parameter(s) ['transitionId']" in p for p in problems)
    assert any("transition_issue requires ['transition_id']" in p for p in problems)
    assert any("create_event has no parameter(s) ['fields']" in p for p in problems)


async def test_a_read_tool_marked_as_writing_is_flagged(tmp_path: Path) -> None:
    listings = await _listings(tmp_path)
    jira = [
        t.model_copy(update={"annotations": mcp_types.ToolAnnotations(read_only_hint=False)})
        if t.name == "get_issue"
        else t
        for t in listings["jira"]
    ]
    problems, _ = check_config(load_tools_config(), {**listings, "jira": jira})
    assert problems == ["jira.get_issue: get_issue says it isn't read-only; reads must not write"]


def test_check_and_list_over_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(STATE_ENV, str(tmp_path / "s.json"))
    monkeypatch.delenv("MEASURED_OPS_TOOLS_CONFIG", raising=False)

    assert main(["check"]) == 0
    assert main(["list", "--server", "jira"]) == 0
    assert main(["list", "--server", "nowhere"]) == 2

    out = capsys.readouterr().out
    assert "ok  calendar.create_event -> calendar.create_event (write)" in out
    assert "transition_issue(key, transition_id)" in out
    assert "update_issue(key, labels?, priority?)" in out
