"""Golden-set format and the bundled draft set (GROW-13)."""

from __future__ import annotations

import copy
import tomllib
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from measured_evals.golden import (
    ASSERTION_KINDS,
    GoldenSetError,
    load_golden_set,
    parse_golden_set,
)

# Items Hassan has approved. Approving one means editing the data file AND this set
# in the same change, so a status can't flip to "approved" unnoticed.
# All 20, approved by Hassan Mansour on 2026-11-17. Approval covers the cases; it
# is not a measured result.
APPROVED_IDS: frozenset[str] = frozenset(
    {
        "loop-needs-an-exit",
        "score-computed-in-code",
        "what-is-a-golden-set",
        "cheap-checks-first",
        "agent-waits-for-approval",
        "fewer-agents",
        "citations-that-resolve",
        "fetched-content-is-data",
        "validate-every-response",
        "paused-run-survives-restart",
        "does-the-judge-agree-with-you",
        "did-it-get-worse",
        "unknown-is-not-zero",
        "a-prompt-change-is-a-code-change",
        "which-half-of-rag-failed",
        "bait-instruction-in-a-source",
        "bait-instruction-in-the-topic",
        "bait-fake-system-tag",
        "unanswerable-no-number-in-sources",
        "unanswerable-no-sources",
    }
)
APPROVED_BY = "Hassan Mansour"
APPROVED_ON = "2026-11-17"


def _raw() -> dict[str, Any]:
    text = (
        resources.files("measured_evals")
        .joinpath("data", "golden", "content_v0.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def test_bundled_set_loads_with_20_items() -> None:
    golden = load_golden_set()
    assert golden.name == "content-v0"
    assert len(golden.items) == 20
    assert len({i.id for i in golden.items}) == 20


def test_only_listed_items_are_approved() -> None:
    golden = load_golden_set()
    assert {i.id for i in golden.approved} == APPROVED_IDS
    assert golden.status_counts()["needs_eval"] == 20 - len(APPROVED_IDS)


def test_every_approval_names_who_and_when() -> None:
    golden = load_golden_set()
    assert len(golden.approved) == 20
    assert {i.approved_by for i in golden.approved} == {APPROVED_BY}
    assert {i.approved_on for i in golden.approved} == {APPROVED_ON}


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"approved_on": "2026-13-45"}, "not a real date"),
        ({"approved_on": "Nov 17"}, "must be a date"),
        ({"approved_on": 20261117}, "must be a date"),
        ({"status": "needs_eval", "approved_by": None}, "only for approved"),
    ],
)
def test_approved_on_is_validated(update: dict[str, Any], message: str) -> None:
    data = _raw()
    item = data["items"][0]
    for key, value in update.items():
        if value is None:
            item.pop(key)
        else:
            item[key] = value
    with pytest.raises(GoldenSetError, match=message):
        parse_golden_set(data)


def test_approved_on_is_optional() -> None:
    data = _raw()
    data["items"][0].pop("approved_on")
    assert parse_golden_set(data).items[0].approved_on is None


def test_set_covers_bait_and_unanswerable_topics() -> None:
    kinds = Counter(i.kind for i in load_golden_set().items)
    assert kinds["injection_bait"] >= 3
    assert kinds["unanswerable"] >= 2
    assert kinds["standard"] >= 10


def test_every_item_has_both_tiers_and_a_lesson() -> None:
    for item in load_golden_set().items:
        tiers = {a.tier for a in (*item.expected, *item.must_not)}
        assert tiers == {"deterministic", "judge"}, item.id
        assert len(item.lesson.split()) >= 12, item.id
        assert all(a.why for a in (*item.expected, *item.must_not)), item.id


def test_every_item_has_a_word_limit_and_resolving_citations() -> None:
    for item in load_golden_set().items:
        kinds = {a.kind for a in item.expected}
        assert {"max_words", "citations_resolve"} <= kinds, item.id


def test_bait_items_forbid_a_canary() -> None:
    for item in load_golden_set().items:
        if item.kind == "injection_bait":
            assert any(a.kind == "contains" for a in item.must_not), item.id


def test_unanswerable_items_require_saying_so() -> None:
    for item in load_golden_set().items:
        if item.kind == "unanswerable":
            assert any(a.kind == "mentions_any" for a in item.expected), item.id


def test_source_ids_are_sequential() -> None:
    for item in load_golden_set().items:
        assert list(item.source_ids) == [f"S{n}" for n in range(1, len(item.sources) + 1)]


def test_by_id() -> None:
    golden = load_golden_set()
    assert golden.by_id("loop-needs-an-exit").difficulty == "easy"
    with pytest.raises(KeyError):
        golden.by_id("nope")


def test_load_from_a_path(tmp_path: Path) -> None:
    source = resources.files("measured_evals").joinpath("data", "golden", "content_v0.toml")
    path = tmp_path / "set.toml"
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(load_golden_set(path).items) == 20


def test_invalid_toml_is_a_golden_set_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("items = [", encoding="utf-8")
    with pytest.raises(GoldenSetError, match="not valid TOML"):
        load_golden_set(path)


def _one() -> dict[str, Any]:
    data = _raw()
    item = copy.deepcopy(data["items"][0])
    # back to a draft: the cases below test the rules for an unreviewed item
    item["status"] = "needs_eval"
    item.pop("approved_by", None)
    item.pop("approved_on", None)
    data["items"] = [item]
    return data


def _break(path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    data = _one()
    node: Any = data["items"][0]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("status",), "done", "status"),
        (("status",), "approved", "approved_by"),
        (("approved_by",), "someone", "only for approved"),
        (("difficulty",), "trivial", "difficulty"),
        (("kind",), "weird", "kind"),
        (("id",), "Has Spaces", "lowercase"),
        (("tags",), [], "tags"),
        (("tags",), ["Not A Slug"], "tag must be"),
        (("topic",), "  ", "topic"),
        (("surprise",), 1, "unknown keys"),
        (("expected",), [], "at least one 'expected'"),
        (("must_not",), [], "at least one 'must_not'"),
        (("sources", 0, "id"), "X1", "S1"),
        (("sources", 1, "id"), "S1", "duplicate source ids"),
        (("expected", 0, "kind"), "vibes", "kind"),
        (("expected", 0, "value"), 0, "positive integer"),
        (("expected", 0, "value"), True, "positive integer"),
        (("expected", 0, "terms"), ["x"], "takes value"),
        (("expected", 0, "why"), "", "why"),
        (
            ("expected", 0),
            {"kind": "contains", "terms": ["x"], "why": "w"},
            "belongs in 'must_not'",
        ),
        (("must_not", 0), {"kind": "max_words", "value": 3, "why": "w"}, "belongs in 'expected'"),
        (("expected", 0), {"kind": "cites", "sources": ["S9"], "why": "w"}, "doesn't have"),
        (("expected", 0), {"kind": "mentions_any", "terms": ["a", "a"], "why": "w"}, "duplicates"),
        (("expected", 0), {"kind": "judge", "why": "w"}, "criterion"),
    ],
)
def test_invalid_items_are_rejected(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    with pytest.raises(GoldenSetError, match=message):
        parse_golden_set(_break(path, value))


def test_error_names_the_item() -> None:
    with pytest.raises(GoldenSetError, match="item loop-needs-an-exit"):
        parse_golden_set(_break(("difficulty",), "nope"))


def test_approved_item_with_reviewer_loads() -> None:
    data = _one()
    data["items"][0] |= {"status": "approved", "approved_by": "reviewer"}
    golden = parse_golden_set(data)
    assert [i.id for i in golden.approved] == ["loop-needs-an-exit"]


def test_set_level_validation() -> None:
    data = _one()
    data["items"].append(copy.deepcopy(data["items"][0]))
    with pytest.raises(GoldenSetError, match="duplicate item id"):
        parse_golden_set(data)
    with pytest.raises(GoldenSetError, match="schema_version"):
        parse_golden_set(_one() | {"schema_version": 2})
    with pytest.raises(GoldenSetError, match="no items"):
        parse_golden_set(_one() | {"items": []})


def test_kind_table_in_the_doc_matches_the_code() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "GOLDEN_SET.md").read_text("utf-8")
    for kind in ASSERTION_KINDS:
        assert f"`{kind}`" in doc, kind
