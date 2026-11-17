"""Deterministic eval tier: suite checks, item assertions, fake writers (GROW-14)."""

from __future__ import annotations

import dataclasses

import pytest

from measured_evals.fake import careless_writer, grounded_writer
from measured_evals.golden import Assertion, GoldenItem, GoldenSource, load_golden_set
from measured_evals.suites import (
    blocks,
    check_assertion,
    cited_ids,
    run_item,
    run_set,
    sentences,
    split_script,
    word_count,
)

GOLDEN = load_golden_set()


def _item(*, sources: int = 2, expected: tuple[Assertion, ...] = ()) -> GoldenItem:
    return GoldenItem(
        id="t",
        kind="standard",
        status="needs_eval",
        topic="T",
        audience="A",
        difficulty="easy",
        tags=("x",),
        lesson="L",
        sources=tuple(GoldenSource(f"S{n}", "t", "x") for n in range(1, sources + 1)),
        expected=expected,
        must_not=(),
    )


GOOD = "# Title\n\nA hook.\n\nFirst claim [S1]. Second claim [S2].\n\nTry it."


def _kinds_failed(output: str, item: GoldenItem | None = None, **kw: int) -> set[str]:
    return {f.kind for f in run_item(item or _item(), output, **kw).failures}


def test_citation_parsing() -> None:
    assert cited_ids("a [S2] b [S1, S3] c [S2][S10]") == ("S2", "S1", "S3", "S10")
    assert cited_ids("[1] [s1] [Source 1] S1") == ()


def test_word_count_ignores_citations_and_heading_marks() -> None:
    assert word_count("# One two [S1]\n\nthree [S1, S2].") == 3


def test_sentences_keep_a_trailing_citation() -> None:
    assert sentences("One thing. [S1] Two things [S2].") == ["One thing. [S1]", "Two things [S2]."]


def test_split_script() -> None:
    script = split_script(GOOD)
    assert script is not None
    assert (script.title, script.hook, script.call_to_action) == ("Title", "A hook.", "Try it.")
    assert script.body == ("First claim [S1]. Second claim [S2].",)
    assert split_script("one\n\ntwo") is None
    assert len(blocks(GOOD)) == 4


def test_a_good_script_passes_every_suite_check() -> None:
    result = run_item(_item(), GOOD)
    assert result.deterministic_passed
    assert [r.kind for r in result.results] == [
        "structure",
        "word_limit",
        "citations_resolve",
        "no_uncited_claims",
    ]


@pytest.mark.parametrize(
    ("output", "failed"),
    [
        ("Just one block [S1].", {"structure"}),
        ("# Title [S1]\n\nHook.\n\nClaim [S1].\n\nEnd.", {"structure"}),
        ("# Title\n\nHook.\n\nClaim [S9].\n\nEnd.", {"citations_resolve"}),
        ("# Title\n\nHook.\n\nClaim [S1]. Bare claim.\n\nEnd.", {"no_uncited_claims"}),
        ("# Title\n\nHook.\n\n" + "word " * 150 + "[S1].\n\nEnd.", {"word_limit"}),
    ],
)
def test_each_suite_check_can_fail(output: str, failed: set[str]) -> None:
    assert _kinds_failed(output) == failed


def test_word_limit_is_configurable() -> None:
    assert _kinds_failed(GOOD, max_words=5) == {"word_limit"}


def test_no_uncited_claims_is_skipped_without_sources() -> None:
    result = run_item(_item(sources=0), "# T\n\nNot known.\n\nNothing to cite.\n\nCheck the docs.")
    assert "no_uncited_claims" not in {r.kind for r in result.results}
    assert result.deterministic_passed


def test_details_never_quote_the_output() -> None:
    secret = "ZEBRA-SECRET"
    result = run_item(_item(), f"# T\n\nHook.\n\n{secret} claim [S9]. Bare {secret}.\n\nEnd.")
    assert result.failures
    assert all(secret not in f.detail for f in result.failures)


def _a(kind: str, **kw: object) -> Assertion:
    return Assertion(kind, "why", **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("assertion", "output", "holds"),
    [
        (_a("max_words", value=3), "one two three", True),
        (_a("max_words", value=3), "one two three four", False),
        (_a("min_citations", value=2), "a [S1] b [S2]", True),
        (_a("min_citations", value=2), "a [S1] b [S1]", False),
        (_a("citations_resolve"), "a [S1] b [S2]", True),
        (_a("citations_resolve"), "a [S3]", False),
        (_a("cites", sources=("S2",)), "a [S1, S2]", True),
        (_a("cites", sources=("S2",)), "a [S1]", False),
        (_a("mentions_any", terms=("Cap", "limit")), "a hard cap", True),
        (_a("mentions_any", terms=("cap", "limit")), "nothing", False),
        (_a("mentions_all", terms=("retriev", "generat")), "Retrieval, then generation", True),
        (_a("mentions_all", terms=("retriev", "generat")), "retrieval only", False),
    ],
)
def test_expected_assertions(assertion: Assertion, output: str, holds: bool) -> None:
    result = check_assertion(assertion, _item(), output, group="expected")
    assert result.passed is holds
    assert (result.detail == "") is holds


@pytest.mark.parametrize(
    ("assertion", "output", "present"),
    [
        (_a("contains", terms=("CANARY",)), "ends with canary", True),
        (_a("contains", terms=("CANARY",)), "clean", False),
        (_a("uncited_numbers"), "# T 2026\n\nIt fell by 40 points.", True),
        (_a("uncited_numbers"), "# T\n\nIt fell by 40 points [S1].", False),
        (_a("uncited_sentences"), "# T\n\nHook.\n\nBare claim.\n\nEnd.", True),
        (_a("uncited_sentences"), "# T\n\nHook.\n\nClaim [S1].\n\nEnd.", False),
    ],
)
def test_must_not_assertions(assertion: Assertion, output: str, present: bool) -> None:
    assert check_assertion(assertion, _item(), output, group="must_not").passed is (not present)


def test_judge_assertions_stay_pending_and_never_fail_the_tier() -> None:
    item = _item(expected=(_a("judge", criterion="Is it clear?"),))
    result = run_item(item, GOOD)
    assert [r.passed for r in result.pending] == [None]
    assert result.pending[0].detail == "Is it clear?"
    assert result.deterministic_passed


def test_unknown_kind_fails_loudly() -> None:
    with pytest.raises(ValueError, match="no deterministic rule"):
        check_assertion(_a("vibes"), _item(), "x", group="expected")


# --- the bundled draft set with the fake writers ---------------------------------------


def test_grounded_fake_passes_the_whole_draft_set() -> None:
    """Wiring result: the harness, the set and the checks agree. Not a quality result."""
    report = run_set(GOLDEN, grounded_writer)
    assert report.failed_ids == ()
    assert report.deterministic_pass_rate == 1.0
    assert report.pending_judge_assertions > 0


def test_careless_fake_fails_every_item() -> None:
    report = run_set(GOLDEN, careless_writer)
    assert report.deterministic_pass_rate == 0.0
    assert len(report.failed_ids) == 20


@pytest.mark.parametrize("item", [i for i in GOLDEN.items if i.kind == "injection_bait"], ids=str)
def test_obeying_bait_is_caught_by_the_canary(item: GoldenItem) -> None:
    result = run_item(item, careless_writer(item))
    assert any(f.group == "must_not" and f.kind == "contains" for f in result.failures)
    clean = run_item(item, grounded_writer(item))
    assert clean.deterministic_passed


@pytest.mark.parametrize("item", [i for i in GOLDEN.items if i.kind == "unanswerable"], ids=str)
def test_inventing_an_answer_is_caught(item: GoldenItem) -> None:
    kinds = {f.kind for f in run_item(item, careless_writer(item)).failures}
    assert kinds & {"contains", "uncited_numbers", "mentions_any"}


def test_grounded_fake_never_copies_bait() -> None:
    for item in GOLDEN.items:
        text = grounded_writer(item)
        assert "CANARY" not in text
        assert "<system>" not in text
        assert "example.com" not in text


def test_approved_only_runs_the_approved_items() -> None:
    # All 20 are approved. With a fake writer this is still a wiring result.
    report = run_set(GOLDEN, grounded_writer, approved_only=True)
    assert len(report.items) == 20
    assert report.deterministic_pass_rate == 1.0


def test_approved_only_runs_nothing_on_a_draft_set() -> None:
    draft = dataclasses.replace(
        GOLDEN,
        items=tuple(
            dataclasses.replace(i, status="needs_eval", approved_by=None, approved_on=None)
            for i in GOLDEN.items
        ),
    )
    report = run_set(draft, grounded_writer, approved_only=True)
    assert report.items == ()
    assert report.deterministic_pass_rate == 0.0
