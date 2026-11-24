"""Demo MCP servers (C5): a tiny Jira-like tracker and a calendar, with demo data only.

They exist so the public demo config (`config/tools.demo.toml`) runs end to end without
an account, and so the tests exercise the real MCP client path (in-process or over stdio)
instead of a mock of it. Their tool names and arguments are deliberately their own: a
real server is mapped onto the same logical operations in its config.

State is one JSON file (`MEASURED_OPS_DEMO_TOOLS_STATE`, default `.data/demo_tools.json`,
git-ignored), seeded with demo issues on first use, so a run paused in one process and
approved in another sees the same tracker. Not for real data, not concurrent-safe beyond
one writer at a time.

    python -m measured_ops.tools.demo_servers jira       # stdio
    python -m measured_ops.tools.demo_servers calendar   # stdio
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

STATE_ENV = "MEASURED_OPS_DEMO_TOOLS_STATE"
DEFAULT_STATE = Path(".data/demo_tools.json")
DEMO_CALENDAR = "demo-personal"
STATUSES = {"11": "To Do", "21": "In Progress", "31": "In Review", "41": "Done"}

SEED: dict[str, Any] = {
    "jira": {
        "issues": {
            "DEMO-1": {"summary": "Draft the weekly plan", "status": "To Do", "labels": []},
            "DEMO-2": {
                "summary": "Wire the approval inbox",
                "status": "In Progress",
                "labels": ["inbox"],
            },
            "DEMO-3": {"summary": "Write the eval report", "status": "To Do", "labels": []},
            "DEMO-4": {"summary": "Record the demo video", "status": "Done", "labels": ["video"]},
        }
    },
    "calendar": {"events": {DEMO_CALENDAR: []}},
}


class DemoState:
    """The demo tracker and calendar, in one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.save(json.loads(json.dumps(SEED)))
        data: dict[str, Any] = json.loads(self.path.read_text("utf-8"))
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
        tmp.replace(self.path)  # atomic: a reader never sees half a file


def _issue_view(key: str, issue: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, **issue, "priority": issue.get("priority", "Medium")}


def jira_server(state: DemoState) -> MCPServer:
    server = MCPServer("demo-tracker", instructions="Demo Jira-like tracker. Demo data only.")

    def _get(data: dict[str, Any], key: str) -> dict[str, Any]:
        issue: dict[str, Any] | None = data["jira"]["issues"].get(key)
        if issue is None:
            raise ToolError(f"issue {key} does not exist")
        return issue

    @server.tool()
    def search_issues(jql: str, limit: int = 20) -> dict[str, Any]:
        """Search issues. Understands `project in (A, B)` and `status = "Name"`, nothing more."""
        data = state.load()
        projects = re.search(r"project in \(([^)]*)\)", jql)
        keep = {p.strip() for p in projects.group(1).split(",")} if projects else None
        status = re.search(r'status\s*=\s*"([^"]+)"', jql)
        out = [
            _issue_view(k, v)
            for k, v in sorted(data["jira"]["issues"].items())
            if (keep is None or k.split("-")[0] in keep)
            and (status is None or v["status"] == status.group(1))
        ]
        return {"issues": out[: max(1, limit)]}

    @server.tool()
    def get_issue(key: str) -> dict[str, Any]:
        """One issue by key."""
        return _issue_view(key, _get(state.load(), key))

    @server.tool()
    def get_transitions(key: str) -> dict[str, Any]:
        """Transitions available from the issue's current status."""
        issue = _get(state.load(), key)
        return {
            "transitions": [
                {"id": tid, "to": name} for tid, name in STATUSES.items() if name != issue["status"]
            ]
        }

    @server.tool()
    def transition_issue(key: str, transition_id: str) -> dict[str, Any]:
        """Move an issue through a transition."""
        data = state.load()
        issue = _get(data, key)
        if transition_id not in STATUSES or STATUSES[transition_id] == issue["status"]:
            raise ToolError(f"transition {transition_id} is not available for {key}")
        issue["status"] = STATUSES[transition_id]
        state.save(data)
        return {"ok": True}

    @server.tool()
    def update_issue(
        key: str, labels: list[str] | None = None, priority: str | None = None
    ) -> dict[str, Any]:
        """Set an issue's labels and/or priority."""
        data = state.load()
        issue = _get(data, key)
        if labels is not None:
            issue["labels"] = sorted(set(labels))
        if priority is not None:
            issue["priority"] = priority
        state.save(data)
        return {"ok": True}

    return server


def calendar_server(state: DemoState) -> MCPServer:
    server = MCPServer("demo-calendar", instructions="Demo calendar. Demo data only.")

    def _events(data: dict[str, Any], calendar_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] | None = data["calendar"]["events"].get(calendar_id)
        if events is None:
            raise ToolError("no such calendar")
        return events

    @server.tool()
    def list_events(calendar_id: str, time_min: str, time_max: str) -> dict[str, Any]:
        """Events overlapping [time_min, time_max)."""
        lo, hi = datetime.fromisoformat(time_min), datetime.fromisoformat(time_max)
        out = [
            e
            for e in _events(state.load(), calendar_id)
            if datetime.fromisoformat(e["start"]) < hi and datetime.fromisoformat(e["end"]) > lo
        ]
        return {"events": out}

    @server.tool()
    def create_event(
        calendar_id: str, summary: str, start: str, end: str, description: str | None = None
    ) -> dict[str, Any]:
        """Create an event; returns it with its id."""
        data = state.load()
        event = {"id": uuid4().hex, "summary": summary, "start": start, "end": end}
        if description:
            event["description"] = description
        _events(data, calendar_id).append(event)
        state.save(data)
        return event

    return server


def state_from_env() -> DemoState:
    return DemoState(Path(os.environ.get(STATE_ENV) or DEFAULT_STATE))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    factories = {"jira": jira_server, "calendar": calendar_server}
    if len(args) != 1 or args[0] not in factories:
        sys.stderr.write("usage: python -m measured_ops.tools.demo_servers {jira,calendar}\n")
        return 2
    factories[args[0]](state_from_env()).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
