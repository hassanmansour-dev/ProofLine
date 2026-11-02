"""Field ownership (docs/ARCHITECTURE.md §3). Week targets check each node returns only owned fields."""

from __future__ import annotations

import typing
from pathlib import Path

from proofline import nodes
from proofline.state import FIELD_OWNERS, InputState, ScriptState


def test_every_node_has_an_ownership_entry() -> None:
    assert set(FIELD_OWNERS) == set(nodes.__all__)


def test_every_non_input_field_has_an_owner() -> None:
    owned = set().union(*FIELD_OWNERS.values())
    inputs = set(typing.get_type_hints(InputState))
    assert set(typing.get_type_hints(ScriptState)) == owned | inputs


def test_only_history_is_shared() -> None:
    seen: dict[str, str] = {}
    for node, fields in FIELD_OWNERS.items():
        for f in fields - {"history"}:
            assert f not in seen, f"{f} owned by both {seen[f]} and {node}"
            seen[f] = node


def test_initial_state_sets_a_default_for_every_later_week_field(tmp_path: Path) -> None:
    from proofline.state import initial_state
    from tests.fakes import make_settings

    state = initial_state("topic", "audience", make_settings(tmp_path).run_config())
    assert state["revision_count"] == 0
    assert state["history"] == []
    for field in ("critique", "score", "approved", "feedback"):
        assert state[field] is None  # type: ignore[literal-required]
