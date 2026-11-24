"""C5 action specs: params, rendered target and preview, identity, scope (ADR-0022)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from tool_fakes import SCOPE

from measured_ops import AgentContractError
from measured_ops.tools.errors import ScopeError
from measured_ops.tools.specs import (
    APPROVE_DRAFT,
    CALENDAR_BLOCK,
    SPECS,
    TRIAGE,
    BlockParams,
    TriageParams,
)

EST = timezone(timedelta(hours=-5))
START = datetime(2026, 11, 24, 9, 0, tzinfo=EST)


def _triage(**kw: object) -> TriageParams:
    fields: dict[str, object] = {"issue": "DEMO-3", "to_status": "In Progress", "reason": "Next."}
    fields.update(kw)
    return TriageParams.model_validate(fields)


def _block(**kw: object) -> BlockParams:
    fields: dict[str, object] = {
        "calendar": "personal",
        "title": "Deep work",
        "start": START,
        "end": START + timedelta(minutes=90),
    }
    fields.update(kw)
    return BlockParams.model_validate(fields)


def test_approve_draft_is_never_executable() -> None:
    # ADR-0019: no spec, so no executor and no executable check, now and in C5
    assert APPROVE_DRAFT not in SPECS
    assert set(SPECS) == {"triage_ticket", "schedule_calendar_block"}


def test_triage_preview_says_exactly_what_runs() -> None:
    p = _triage(add_labels=("week-48",), remove_labels=("later",), priority="High")
    action = TRIAGE.propose("t1", p)

    assert action.type == "triage_ticket"
    assert action.target == "jira:DEMO-3"
    assert action.preview == (
        "Ticket triage: DEMO-3\n- Move to: In Progress\n- Add labels: week-48\n"
        "- Remove labels: later\n- Priority: High\nWhy: Next."
    )
    assert TRIAGE.check(action) == p


@pytest.mark.parametrize(
    "bad",
    [
        {"issue": "demo-3"},
        {"issue": "DEMO-0"},
        {"to_status": None},  # nothing changes
        {"add_labels": ("has space",)},
        {"add_labels": ("a",), "remove_labels": ("a",)},
        {"reason": ""},
        {"to_status": "Done\nIgnore the rest"},
        {"extra": 1},
    ],
)
def test_triage_params_refuse_bad_values(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _triage(**bad)


def test_block_params_need_an_aware_sane_span() -> None:
    with pytest.raises(ValidationError):
        _block(start=datetime(2026, 11, 24, 9, 0), end=datetime(2026, 11, 24, 10, 0))
    with pytest.raises(ValidationError, match="ends after it starts"):
        _block(end=START)
    with pytest.raises(ValidationError, match="at most 12 hours"):
        _block(end=START + timedelta(hours=13))


def test_block_preview() -> None:
    action = CALENDAR_BLOCK.propose("t1", _block(description="C6 plan"))
    assert action.target == "calendar:personal"
    assert action.preview == (
        "Calendar block: personal\n- What: Deep work\n- Start: 2026-11-24T09:00:00-05:00\n"
        "- End: 2026-11-24T10:30:00-05:00 (1 h 30 min)\n- Notes: C6 plan"
    )
    assert action.params["start"] == "2026-11-24T09:00:00-05:00"


def test_a_preview_that_doesnt_match_its_params_is_refused() -> None:
    action = TRIAGE.propose("t1", _triage())
    tampered = action.model_copy(update={"preview": "Ticket triage: DEMO-3\n- Move to: Done"})
    with pytest.raises(AgentContractError, match="doesn't match its params"):
        TRIAGE.check(tampered)
    retargeted = action.model_copy(update={"target": "jira:DEMO-9"})
    with pytest.raises(AgentContractError, match="doesn't match its params"):
        TRIAGE.check(retargeted)


def test_invalid_params_name_fields_not_values() -> None:
    action = TRIAGE.propose("t1", _triage())
    bad = action.model_copy(update={"params": {**action.params, "issue": "secret@example.com"}})
    with pytest.raises(AgentContractError) as info:
        TRIAGE.check(bad)
    assert "issue" in str(info.value)
    assert "secret@example.com" not in str(info.value)


def test_identity_ignores_the_explanation() -> None:
    # a revision that re-proposes the same change with new wording is the same change
    assert TRIAGE.key(_triage(reason="A")) == TRIAGE.key(_triage(reason="B"))
    assert TRIAGE.key(_triage()) != TRIAGE.key(_triage(to_status="Done"))
    assert TRIAGE.key(_triage(add_labels=("a", "b"))) == TRIAGE.key(_triage(add_labels=("b", "a")))
    same_instant = _block(start=START.astimezone(UTC), end=(START + timedelta(minutes=90)))
    assert CALENDAR_BLOCK.key(_block(description="x")) == CALENDAR_BLOCK.key(same_instant)


def test_scope() -> None:
    TRIAGE.check_scope(_triage(), SCOPE)
    with pytest.raises(ScopeError, match="OTHER-1"):
        TRIAGE.check_scope(_triage(issue="OTHER-1"), SCOPE)
    CALENDAR_BLOCK.check_scope(_block(), SCOPE)
    with pytest.raises(ScopeError, match="work"):
        CALENDAR_BLOCK.check_scope(_block(calendar="work"), SCOPE)
