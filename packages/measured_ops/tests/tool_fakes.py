"""Test-only tool fakes (C5): in-memory Jira and calendar ports, and a stub agent that
reads through a read port and proposes executable actions with the specs.

Fixtures, never evidence: the issue keys, statuses and titles here are made up.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from measured_ops import AgentGraph, AgentInput, AgentOutput, AgentResult, Task
from measured_ops.tools.errors import ToolError, ToolUnavailableError
from measured_ops.tools.executors import CalendarBlockExecutor, Executors, TriageExecutor
from measured_ops.tools.ports import (
    CalendarEvent,
    CalendarReads,
    JiraIssue,
    JiraReads,
    JiraTransition,
)
from measured_ops.tools.specs import CALENDAR_BLOCK, TRIAGE, BlockParams, ToolScope, TriageParams

STATUSES = ("To Do", "In Progress", "Done")


class FakeJira:
    """Both Jira ports over a dict. `fail_next` makes the next N calls raise `error`;
    (`fail_only`: just those calls); `ignore_writes` accepts writes without applying
    them (to test verification)."""

    def __init__(self, issues: dict[str, dict[str, Any]] | None = None) -> None:
        self.issues = issues or {
            "DEMO-1": {"summary": "Plan", "status": "To Do", "labels": [], "priority": "Medium"},
            "DEMO-2": {"summary": "Build", "status": "In Progress", "labels": ["x"]},
            "OTHER-1": {"summary": "Not ours", "status": "To Do", "labels": []},
        }
        self.calls: list[str] = []
        self.writes: list[tuple[str, Any]] = []
        self.fail_next = 0
        self.fail_only: set[str] | None = None  # only these calls fail (None: any)
        self.error: Exception = ToolUnavailableError("fake: unreachable")
        self.ignore_writes = False

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_next and (self.fail_only is None or name in self.fail_only):
            self.fail_next -= 1
            raise self.error

    def _issue(self, key: str) -> JiraIssue:
        raw = self.issues[key]
        return JiraIssue(
            key=key,
            summary=raw["summary"],
            status=raw["status"],
            labels=tuple(raw.get("labels", [])),
            priority=raw.get("priority"),
        )

    async def search(self, jql: str, *, limit: int = 20) -> list[JiraIssue]:
        self._call("search")
        want = None
        if 'status = "' in jql:
            want = jql.split('status = "', 1)[1].split('"', 1)[0]
        return [
            self._issue(k)
            for k in sorted(self.issues)
            if k.startswith("DEMO-") and (want is None or self.issues[k]["status"] == want)
        ][:limit]

    async def get_issue(self, key: str) -> JiraIssue:
        self._call("get_issue")
        return self._issue(key)

    async def transitions(self, key: str) -> list[JiraTransition]:
        self._call("transitions")
        current = self.issues[key]["status"]
        return [
            JiraTransition(id=str(i), to_status=s) for i, s in enumerate(STATUSES) if s != current
        ]

    async def transition(self, key: str, transition_id: str) -> None:
        self._call("transition")
        self.writes.append(("transition", (key, transition_id)))
        if not self.ignore_writes:
            self.issues[key]["status"] = STATUSES[int(transition_id)]

    async def update(
        self, key: str, *, labels: list[str] | None = None, priority: str | None = None
    ) -> None:
        self._call("update")
        self.writes.append(("update", (key, labels, priority)))
        if self.ignore_writes:
            return
        if labels is not None:
            self.issues[key]["labels"] = list(labels)
        if priority is not None:
            self.issues[key]["priority"] = priority


class FakeCalendar:
    """Both calendar ports over a list per alias."""

    def __init__(self) -> None:
        self.events: dict[str, list[CalendarEvent]] = {"personal": []}
        self.calls: list[str] = []

    async def list_events(
        self, calendar: str, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        self.calls.append("list_events")
        return [
            e
            for e in self.events[calendar]
            if e.start is not None and e.end is not None and e.start < end and e.end > start
        ]

    async def create_event(
        self,
        calendar: str,
        *,
        title: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
    ) -> str:
        self.calls.append("create_event")
        event = CalendarEvent(id=uuid4().hex, title=title, start=start, end=end)
        self.events[calendar].append(event)
        return event.id


SCOPE = ToolScope(jira_projects=frozenset({"DEMO"}), calendars=frozenset({"personal"}))


def fake_executors(jira: FakeJira, calendar: FakeCalendar | None = None) -> Executors:
    calendar = calendar or FakeCalendar()
    return Executors([TriageExecutor(jira, jira), CalendarBlockExecutor(calendar, calendar)], SCOPE)


class _State(TypedDict, total=False):
    task: Task
    result: AgentResult


class TriageStubAgent:
    """Reads the tracker through a read port only, then proposes moving the first "To Do"
    issue to "In Progress" (and, optionally, a calendar block). The only way it can
    change anything is by proposing: it holds no write port.

    `inputs` overrides: `tamper` (preview text that doesn't match the params),
    `issue` (force an issue key), `block` (also propose a calendar block, BlockParams JSON)."""

    name = "ops"

    def __init__(self, jira: JiraReads, calendar: CalendarReads | None = None) -> None:
        self.jira = jira
        self.calendar = calendar
        self.runs = 0

    def build(self) -> AgentGraph:
        agent = self

        async def propose(state: _State) -> dict[str, Any]:
            agent.runs += 1
            task = state["task"]
            todo = await agent.jira.search('status = "To Do"', limit=5)
            key = str(task.inputs.get("issue") or (todo[0].key if todo else ""))
            if not key:
                result = AgentResult(
                    task_id=task.id, agent=agent.name, status="failed", summary="", error="none"
                )
                return {"result": result}
            params = TriageParams(issue=key, to_status="In Progress", reason="Next up this week.")
            action = TRIAGE.propose(task.id, params)
            if "tamper" in task.inputs:
                action = action.model_copy(update={"preview": str(task.inputs["tamper"])})
            actions = [action]
            if "block" in task.inputs:
                block = BlockParams.model_validate(task.inputs["block"])
                actions.append(CALENDAR_BLOCK.propose(task.id, block))
            result = AgentResult(
                task_id=task.id,
                agent=agent.name,
                status="succeeded",
                summary=f"Read {len(todo)} open issue(s).",
                actions=tuple(actions),
            )
            return {"result": result}

        builder = StateGraph(_State, input_schema=AgentInput, output_schema=AgentOutput)
        builder.add_node("propose", propose)
        builder.add_edge(START, "propose")
        builder.add_edge("propose", END)
        return builder.compile()


def broken_jira(error: Exception | None = None) -> FakeJira:
    jira = FakeJira()
    jira.fail_next = 10**6
    jira.error = error or ToolError("fake: refused")
    return jira
