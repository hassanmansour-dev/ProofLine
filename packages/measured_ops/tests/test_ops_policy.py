"""Approval policy table: config-driven, unknown action types need approval, and a
failed or missing eval raises "auto" to approval but never lowers anything (ADR-0018)."""

from __future__ import annotations

import itertools
import logging
from pathlib import Path

import pytest
from ops_fakes import FAILED, PASSED, make_action
from pydantic import ValidationError

from measured_ops import ActionBlockedError, ApprovalDecision, load_policy
from measured_ops.policy import ApprovalPolicy

AUTO = [
    "draft_content",
    "research",
    "schedule_internal_block",
    "triage_ticket",
    "run_tests",
    "run_evals",
]
APPROVAL = [
    "publish_public",
    "approve_draft",  # C8: the content agent's draft, never executed (ADR-0019)
    "deploy_production",
    "spend_money",
    "message_person",
    "change_learner_data",
]
NEVER = ["bulk_export_personal_data", "act_outside_own_systems"]


def test_demo_table_matches_grow_69() -> None:
    policy = load_policy()
    assert {t for t, d in policy.actions.items() if d == "auto"} == set(AUTO)
    assert {t for t, d in policy.actions.items() if d == "approval"} == set(APPROVAL)
    assert {t for t, d in policy.actions.items() if d == "never"} == set(NEVER)


@pytest.mark.parametrize("action_type", AUTO)
def test_internal_work_runs_automatically(action_type: str) -> None:
    action = make_action("t1", type=action_type, risk_level="low", eval_scores=PASSED)
    assert load_policy().decide(action) == "auto"


@pytest.mark.parametrize("action_type", APPROVAL)
def test_real_impact_waits_for_a_human(action_type: str) -> None:
    action = make_action("t1", type=action_type, risk_level="low")
    assert load_policy().requires_approval(action)


def test_unknown_action_type_defaults_to_approval() -> None:
    policy = load_policy()
    assert policy.decide_type("wire_money_to_stranger") == "approval"
    assert policy.decide(make_action("t1", type="brand_new_action", risk_level="low")) == "approval"


def test_empty_table_approves_nothing_automatically() -> None:
    policy = ApprovalPolicy(version=1)
    assert policy.decide(make_action("t1", type="research", risk_level="low")) == "approval"


def test_high_risk_overrides_an_auto_row() -> None:
    action = make_action("t1", type="research", risk_level="high")
    assert load_policy().decide(action) == "approval"


@pytest.mark.parametrize("action_type", NEVER)
@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_never_rows_are_blocked_at_every_risk_level(action_type: str, risk: str) -> None:
    policy = load_policy()
    action = make_action("t1", type=action_type, risk_level=risk)
    assert policy.decide(action) == "never"
    assert policy.is_blocked(action)
    assert not policy.requires_approval(action)  # never sent to the inbox


@pytest.mark.parametrize("action_type", NEVER)
def test_blocked_action_is_logged(action_type: str, caplog: pytest.LogCaptureFixture) -> None:
    action = make_action("t1", type=action_type, risk_level="low")
    with caplog.at_level(logging.WARNING, logger="measured_ops.policy"):
        load_policy().decide(action)
    [record] = caplog.records
    assert action.id in record.getMessage()
    assert action_type in record.getMessage()


@pytest.mark.parametrize("action_type", NEVER)
def test_no_approval_unblocks_a_never_action(action_type: str) -> None:
    policy = load_policy()
    action = make_action("t1", type=action_type, risk_level="low")
    with pytest.raises(ActionBlockedError, match="no approval can unblock it"):
        policy.check_decision(action, ApprovalDecision(action_id=action.id, approved=True))
    # a rejection is harmless and allowed
    policy.check_decision(action, ApprovalDecision(action_id=action.id, approved=False))


def test_approval_tier_accepts_a_human_approval() -> None:
    action = make_action("t1", type="publish_public")
    decision = ApprovalDecision(action_id=action.id, approved=True)
    load_policy().check_decision(action, decision)  # no error


def test_decision_for_another_action_is_refused() -> None:
    action = make_action("t1")
    with pytest.raises(ValueError, match="not"):
        load_policy().check_decision(action, ApprovalDecision(action_id="other", approved=True))


def test_never_is_not_logged_for_other_tiers(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="measured_ops.policy"):
        load_policy().decide(make_action("t1", type="research", risk_level="high"))
    assert caplog.records == []


def test_loads_a_private_table_from_a_path(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text('version = 1\n[actions]\npublish_public = "auto"\n', encoding="utf-8")
    policy = load_policy(path)
    action = make_action("t1", type="publish_public", risk_level="low", eval_scores=PASSED)
    assert policy.decide(action) == "auto"
    assert policy.decide_type("research") == "approval"  # not listed here → approval


def test_private_table_can_add_never_rows(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text('version = 1\n[actions]\nsend_invoice = "never"\n', encoding="utf-8")
    action = make_action("t1", type="send_invoice", risk_level="low")
    assert load_policy(path).decide(action) == "never"


@pytest.mark.parametrize(
    "toml",
    [
        'version = 1\n[actions]\nresearch = "maybe"\n',  # unknown decision
        'version = 1\n[actions]\n"Publish Post" = "auto"\n',  # not snake_case
        '[actions]\nresearch = "auto"\n',  # no version
        'version = 2\n[actions]\nresearch = "auto"\n',  # unsupported version
        'version = 1\nresearch = "auto"\n',  # row outside [actions]
    ],
)
def test_invalid_tables_fail_to_load(tmp_path: Path, toml: str) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy(path)


def test_policy_is_frozen() -> None:
    with pytest.raises(ValidationError):
        load_policy().actions = {}  # type: ignore[misc]


# --- eval escalation (C7, ADR-0018) -----------------------------------------------------

RANK = {"auto": 0, "approval": 1, "never": 2}
ROWS = {"auto_row": "auto", "approval_row": "approval", "never_row": "never"}
MATRIX_POLICY = ApprovalPolicy(version=1, actions=ROWS)
EVALS = {"passed": PASSED, "failed": FAILED, "missing": None}


@pytest.mark.parametrize(
    ("action_type", "risk", "eval_name"),
    list(itertools.product([*ROWS, "unlisted"], ["low", "medium", "high"], EVALS)),
)
def test_eval_only_ever_raises_auto_to_approval(
    action_type: str, risk: str, eval_name: str
) -> None:
    scores = EVALS[eval_name]
    action = make_action("t1", type=action_type, risk_level=risk, eval_scores=scores)
    baseline = make_action("t1", type=action_type, risk_level=risk, eval_scores=PASSED)
    got = MATRIX_POLICY.decide(action)
    without_eval = MATRIX_POLICY.decide(baseline)

    assert RANK[got] >= RANK[without_eval]  # never lowered
    if eval_name == "passed":
        assert got == without_eval
    else:
        # fail closed: a failed or missing eval makes "auto" wait; nothing else changes
        assert got == ("approval" if without_eval == "auto" else without_eval)


def test_failed_eval_never_unblocks_or_hides_a_never_action(
    caplog: pytest.LogCaptureFixture,
) -> None:
    action = make_action("t1", type="never_row", eval_scores=FAILED)
    with caplog.at_level(logging.WARNING, logger="measured_ops.policy"):
        assert MATRIX_POLICY.decide(action) == "never"
    assert "blocked action" in caplog.text


def test_failed_eval_is_logged_by_kind_without_values(caplog: pytest.LogCaptureFixture) -> None:
    action = make_action("t1", type="auto_row", risk_level="low", eval_scores=FAILED)
    with caplog.at_level(logging.INFO, logger="measured_ops.policy"):
        assert MATRIX_POLICY.decide(action) == "approval"
    assert "failed checks ['pii']" in caplog.text


def test_missing_eval_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    action = make_action("t1", type="auto_row", risk_level="low")
    with caplog.at_level(logging.INFO, logger="measured_ops.policy"):
        assert MATRIX_POLICY.decide(action) == "approval"
    assert "no eval scores" in caplog.text


def test_blocked_action_log_names_id_and_type_never_the_target(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # a target can be an address or a phone number (message_person, ...): keep it out
    action = make_action(
        "t1", type="bulk_export_personal_data", target="jane.doe@example.com, (514) 555-0123"
    )
    with caplog.at_level(logging.DEBUG, logger="measured_ops"):
        assert load_policy().decide(action) == "never"

    assert action.id in caplog.text
    assert "bulk_export_personal_data" in caplog.text
    assert "jane.doe" not in caplog.text
    assert "555-0123" not in caplog.text
