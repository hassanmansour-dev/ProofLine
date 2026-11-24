"""C5 executors over fake ports: read first, write what differs, verify, dedupe (ADR-0022)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ops_fakes import make_action
from tool_fakes import SCOPE, FakeCalendar, FakeJira, fake_executors

from measured_ops import AgentContractError
from measured_ops.tools.errors import ScopeError, ToolConfigError, ToolError
from measured_ops.tools.executors import (
    CalendarBlockExecutor,
    Execution,
    Executors,
    TriageExecutor,
    execute_action,
)
from measured_ops.tools.specs import CALENDAR_BLOCK, TRIAGE, ActionSpec, BlockParams, TriageParams

pytestmark = pytest.mark.anyio
START = datetime(2026, 11, 24, 14, 0, tzinfo=UTC)


def _move(issue: str = "DEMO-1", **kw: object) -> TriageParams:
    fields: dict[str, object] = {"issue": issue, "to_status": "In Progress", "reason": "Next."}
    fields.update(kw)
    return TriageParams.model_validate(fields)


def _block() -> BlockParams:
    return BlockParams(
        calendar="personal", title="Deep work", start=START, end=START + timedelta(hours=1)
    )


async def test_triage_moves_labels_and_prioritises_then_verifies() -> None:
    jira = FakeJira()
    p = _move(add_labels=("week-48",), priority="High")

    result = await TriageExecutor(jira, jira).run(p)

    assert result == Execution(
        "applied", "DEMO-1: moved to In Progress; labels +week-48; priority High"
    )
    assert jira.issues["DEMO-1"]["status"] == "In Progress"
    assert jira.issues["DEMO-1"]["labels"] == ["week-48"]
    assert jira.calls == ["get_issue", "transitions", "transition", "update", "get_issue"]


async def test_triage_already_in_place_writes_nothing() -> None:
    jira = FakeJira()
    result = await TriageExecutor(jira, jira).run(_move("DEMO-2", add_labels=("x",)))
    assert result.status == "already_applied"
    assert jira.writes == []


async def test_triage_status_match_ignores_case() -> None:
    jira = FakeJira()
    result = await TriageExecutor(jira, jira).run(_move("DEMO-2", to_status="in progress"))
    assert result.status == "already_applied"


async def test_triage_without_a_matching_transition_fails_loudly() -> None:
    jira = FakeJira()
    with pytest.raises(ToolError, match="no transition from 'To Do' to 'Blocked'"):
        await TriageExecutor(jira, jira).run(_move(to_status="Blocked"))
    assert jira.writes == []


async def test_triage_that_does_not_take_is_an_error() -> None:
    jira = FakeJira()
    jira.ignore_writes = True
    with pytest.raises(ToolError, match="didn't take effect"):
        await TriageExecutor(jira, jira).run(_move())


async def test_calendar_block_created_once() -> None:
    cal = FakeCalendar()
    ex = CalendarBlockExecutor(cal, cal)

    first = await ex.run(_block())
    again = await ex.run(_block())  # a retry after a crash: already there

    assert first.status == "applied"
    assert first.ref
    assert again == Execution("already_applied", "block already on the calendar", first.ref)
    assert len(cal.events["personal"]) == 1


def test_registry_refuses_approve_draft_and_unknown_types() -> None:
    class DraftExecutor:
        spec: ActionSpec[TriageParams]

        def __init__(self) -> None:
            fake = TRIAGE.__class__()
            fake.type = "approve_draft"
            self.spec = fake

        async def run(self, params: object) -> Execution:
            raise AssertionError

    with pytest.raises(ValueError, match="approve_draft never gets an executor"):
        Executors([DraftExecutor()], SCOPE)
    jira = FakeJira()
    with pytest.raises(ValueError, match="two executors"):
        Executors([TriageExecutor(jira, jira), TriageExecutor(jira, jira)], SCOPE)


async def test_types_without_a_spec_are_manual() -> None:
    outcome = await execute_action(make_action("t", type="approve_draft"), None)
    assert (outcome.status, outcome.detail) == (
        "manual",
        "approved draft: pasted by hand, never executed",
    )
    outcome = await execute_action(make_action("t", type="publish_public"), None)
    assert outcome.status == "manual"


async def test_an_executable_type_never_runs_without_tools() -> None:
    action = TRIAGE.propose("t", _move())
    with pytest.raises(ToolConfigError, match="no tools are configured"):
        await execute_action(action, None)
    jira = FakeJira()
    only_calendar = Executors([CalendarBlockExecutor(FakeCalendar(), FakeCalendar())], SCOPE)
    with pytest.raises(ToolConfigError, match="no executor configured for triage_ticket"):
        await execute_action(action, only_calendar)
    assert jira.calls == []


async def test_re_checked_before_running() -> None:
    jira = FakeJira()
    executors = fake_executors(jira)
    tampered = TRIAGE.propose("t", _move()).model_copy(update={"preview": "Looks harmless."})
    with pytest.raises(AgentContractError):
        await execute_action(tampered, executors)
    with pytest.raises(ScopeError):
        await execute_action(TRIAGE.propose("t", _move("OTHER-1")), executors)
    assert jira.calls == []


async def test_same_change_proposed_again_is_already_applied_without_a_write() -> None:
    # ADR-0019: a revision may re-propose an approved action; it must not run twice
    jira = FakeJira()
    executors = fake_executors(jira)
    first = await execute_action(TRIAGE.propose("t1", _move(reason="A")), executors)
    writes = len(jira.writes)

    again = await execute_action(TRIAGE.propose("t2", _move(reason="B")), executors)

    assert first.status == "applied"
    assert again.status == "already_applied"  # found in place by reading the tracker
    assert again.key == first.key
    assert len(jira.writes) == writes


async def test_outcome_for_a_block_carries_the_event_id() -> None:
    cal = FakeCalendar()
    outcome = await execute_action(
        CALENDAR_BLOCK.propose("t", _block()), fake_executors(FakeJira(), cal)
    )
    assert outcome.status == "applied"
    assert outcome.ref == cal.events["personal"][0].id


async def test_a_re_proposed_change_is_checked_against_the_tracker_not_the_runs_memory() -> None:
    # Review: an earlier "applied" outcome is stale evidence. If the ticket was moved back
    # since, the same change proposed and approved again must be applied again.
    jira = FakeJira()
    executors = fake_executors(jira)
    first = await execute_action(TRIAGE.propose("t1", _move(reason="A")), executors)
    assert first.status == "applied"
    jira.issues["DEMO-1"]["status"] = "To Do"  # someone moved it back in Jira

    again = await execute_action(TRIAGE.propose("t2", _move(reason="B")), executors)

    assert again.status == "applied"
    assert jira.issues["DEMO-1"]["status"] == "In Progress"
