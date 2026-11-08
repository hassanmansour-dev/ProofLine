"""Approval policy table: config-driven, and unknown action types need approval."""

from __future__ import annotations

from pathlib import Path

import pytest
from ops_fakes import make_action
from pydantic import ValidationError

from measured_ops import load_policy
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
    "deploy_production",
    "spend_money",
    "message_person",
    "change_learner_data",
]


def test_demo_table_matches_grow_69() -> None:
    policy = load_policy()
    assert {t for t, d in policy.actions.items() if d == "auto"} == set(AUTO)
    assert {t for t, d in policy.actions.items() if d == "approval"} == set(APPROVAL)


@pytest.mark.parametrize("action_type", AUTO)
def test_internal_work_runs_automatically(action_type: str) -> None:
    assert load_policy().decide(make_action("t1", type=action_type, risk_level="low")) == "auto"


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


def test_loads_a_private_table_from_a_path(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text('version = 1\n[actions]\npublish_public = "auto"\n', encoding="utf-8")
    policy = load_policy(path)
    assert policy.decide(make_action("t1", type="publish_public", risk_level="low")) == "auto"
    assert policy.decide_type("research") == "approval"  # not listed here → approval


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
