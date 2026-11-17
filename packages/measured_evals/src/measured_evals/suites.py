"""Eval suites over a golden set: the deterministic tier (GROW-14).

`run_item(item, output)` checks one output against one golden item and returns a
result per assertion. Everything here is computed in code: no model, no key, same
answer every time. `judge` assertions are returned as *pending*, never guessed;
the model-graded tier (see `docs/EVALS.md`) or a person answers them.

Two groups of checks run on every output:

1. **Suite checks**, the same for every item:
   - `structure`: a title line, a hook, a body and a call to action (at least
     three blocks separated by blank lines), and no citation in the title;
   - `word_limit`: at most `max_words` words (default 140), citations not counted;
   - `citations_resolve`: every `[S1]`-style citation names a source of the item;
   - `no_uncited_claims`: when the item has sources, every body sentence carries a
     citation. Heuristic: the body is every block except the title, the hook and
     the last block (the call to action). It can't tell a claim from a transition,
     so it is strict on purpose: in the body, cite or cut.
2. **The item's own assertions** (`expected` and `must_not`, `measured_evals.golden`).

An output is a plain string, so this package knows no app types (ADR-0012).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from measured_evals.golden import Assertion, GoldenItem, GoldenSet

DEFAULT_MAX_WORDS = 140
SUITE_CHECKS: tuple[str, ...] = (
    "structure",
    "word_limit",
    "citations_resolve",
    "no_uncited_claims",
)

Group = Literal["suite", "expected", "must_not"]

# [S1], [S1, S2], [S1][S2]
_CITATION_GROUP = re.compile(r"\[\s*S\d{1,3}(?:\s*,\s*S\d{1,3})*\s*\]")
_SOURCE_ID = re.compile(r"S\d{1,3}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"\d")
_LEADING_CITATIONS = re.compile(rf"(?:{_CITATION_GROUP.pattern}\s*)+")


def cited_ids(text: str) -> tuple[str, ...]:
    """Source ids cited in `text`, in order of first appearance, without duplicates."""
    seen: dict[str, None] = {}
    for group in _CITATION_GROUP.findall(text):
        for source_id in _SOURCE_ID.findall(group):
            seen.setdefault(source_id)
    return tuple(seen)


def strip_citations(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _CITATION_GROUP.sub("", text))


def word_count(text: str) -> int:
    """Words in `text`, not counting citation marks or a leading Markdown `#`."""
    return sum(1 for w in strip_citations(text).split() if any(c.isalnum() for c in w))


def blocks(text: str) -> list[str]:
    """Blocks of text separated by blank lines."""
    return [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]


def sentences(text: str) -> list[str]:
    """Sentences of a block. A citation written after the full stop stays with its sentence."""
    out: list[str] = []
    for raw in _SENTENCE_END.split(" ".join(text.split())):
        part = raw
        lead = _LEADING_CITATIONS.match(part)
        if out and lead:
            out[-1] = f"{out[-1]} {lead.group(0).strip()}"
            part = part[lead.end() :]
        if part.strip():
            out.append(part.strip())
    return out


@dataclass(frozen=True, slots=True)
class Script:
    """An output split the way the pipeline writes it: title, hook, body, call to action."""

    title: str
    hook: str
    body: tuple[str, ...]
    call_to_action: str


def split_script(text: str) -> Script | None:
    """Split into title / hook / body / call to action, or None if there are fewer than
    three blocks. With exactly three, the middle block is both hook and body."""
    parts = blocks(text)
    if len(parts) < 3:
        return None
    body = tuple(parts[2:-1]) if len(parts) > 3 else (parts[1],)
    return Script(parts[0].lstrip("# ").strip(), parts[1], body, parts[-1])


@dataclass(frozen=True, slots=True)
class AssertionResult:
    """One check on one output. `passed` is None for a judge assertion not yet answered.

    `detail` names the kind of problem and counts; it never quotes the output.
    """

    group: Group
    kind: str
    passed: bool | None
    detail: str = ""
    why: str = ""

    @property
    def pending(self) -> bool:
        return self.passed is None


@dataclass(frozen=True, slots=True)
class ItemResult:
    item_id: str
    results: tuple[AssertionResult, ...]

    @property
    def deterministic_passed(self) -> bool:
        """True if every deterministic check passed. Says nothing about judge assertions."""
        return all(r.passed for r in self.results if not r.pending)

    @property
    def failures(self) -> tuple[AssertionResult, ...]:
        return tuple(r for r in self.results if r.passed is False)

    @property
    def pending(self) -> tuple[AssertionResult, ...]:
        return tuple(r for r in self.results if r.pending)


@dataclass(frozen=True, slots=True)
class SuiteReport:
    golden_set: str
    items: tuple[ItemResult, ...]

    @property
    def deterministic_pass_rate(self) -> float:
        """Share of items whose deterministic checks all passed. 0.0 for an empty run."""
        if not self.items:
            return 0.0
        return sum(1 for i in self.items if i.deterministic_passed) / len(self.items)

    @property
    def failed_ids(self) -> tuple[str, ...]:
        return tuple(i.item_id for i in self.items if not i.deterministic_passed)

    @property
    def pending_judge_assertions(self) -> int:
        return sum(len(i.pending) for i in self.items)


# --- suite checks ----------------------------------------------------------------------


def _unresolved(text: str, source_ids: Iterable[str]) -> list[str]:
    known = set(source_ids)
    return [c for c in cited_ids(text) if c not in known]


def _uncited(parts: Iterable[str]) -> int:
    return sum(1 for block in parts for s in sentences(block) if not cited_ids(s))


def _suite_results(item: GoldenItem, output: str, max_words: int) -> list[AssertionResult]:
    script = split_script(output)
    if script is None:
        structure = AssertionResult("suite", "structure", False, "fewer than three blocks")
    elif cited_ids(script.title):
        structure = AssertionResult("suite", "structure", False, "citation in the title")
    else:
        structure = AssertionResult("suite", "structure", True)
    words = word_count(output)
    unresolved = _unresolved(output, item.source_ids)
    results = [
        structure,
        AssertionResult(
            "suite",
            "word_limit",
            words <= max_words,
            "" if words <= max_words else f"{words} words, limit {max_words}",
        ),
        AssertionResult(
            "suite",
            "citations_resolve",
            not unresolved,
            "" if not unresolved else f"{len(unresolved)} citation(s) to unknown sources",
        ),
    ]
    if item.sources and script is not None:
        missing = _uncited(script.body)
        results.append(
            AssertionResult(
                "suite",
                "no_uncited_claims",
                missing == 0,
                "" if missing == 0 else f"{missing} body sentence(s) without a citation",
            )
        )
    return results


# --- item assertions -------------------------------------------------------------------


def _present(terms: Iterable[str], text: str) -> list[str]:
    lowered = text.lower()
    return [t for t in terms if t.lower() in lowered]


def _numbers_without_citation(output: str) -> int:
    parts = blocks(output)[1:]  # the title carries no citations by rule
    return sum(
        1
        for block in parts
        for s in sentences(block)
        if _NUMBER.search(strip_citations(s)) and not cited_ids(s)
    )


def _holds(assertion: Assertion, item: GoldenItem, output: str) -> tuple[bool, str]:
    """Whether a deterministic assertion's condition is true of `output`, with a detail."""
    kind = assertion.kind
    if kind == "max_words":
        words, limit = word_count(output), assertion.value or 0
        return words <= limit, f"{words} words, limit {limit}"
    if kind == "min_citations":
        count, need = len(cited_ids(output)), assertion.value or 0
        return count >= need, f"{count} distinct source(s) cited, need {need}"
    if kind == "citations_resolve":
        unresolved = _unresolved(output, item.source_ids)
        return not unresolved, f"{len(unresolved)} citation(s) to unknown sources"
    if kind == "cites":
        cited = set(cited_ids(output))
        missing = [s for s in assertion.sources if s not in cited]
        return not missing, f"{len(missing)} required source(s) not cited"
    if kind == "mentions_any":
        return bool(_present(assertion.terms, output)), "none of the terms appear"
    if kind == "mentions_all":
        missing_terms = len(assertion.terms) - len(_present(assertion.terms, output))
        return missing_terms == 0, f"{missing_terms} term(s) missing"
    if kind == "contains":
        found = len(_present(assertion.terms, output))
        return found > 0, f"{found} forbidden term(s) present"
    if kind == "uncited_numbers":
        count = _numbers_without_citation(output)
        return count > 0, f"{count} sentence(s) with a number and no citation"
    if kind == "uncited_sentences":
        script = split_script(output)
        count = _uncited(script.body) if script else _uncited(blocks(output))
        return count > 0, f"{count} body sentence(s) without a citation"
    raise ValueError(f"no deterministic rule for assertion kind {kind!r}")


def check_assertion(
    assertion: Assertion, item: GoldenItem, output: str, *, group: Literal["expected", "must_not"]
) -> AssertionResult:
    """Evaluate one assertion. `expected` passes when it holds; `must_not` when it doesn't."""
    if assertion.tier == "judge":
        return AssertionResult(group, "judge", None, assertion.criterion or "", assertion.why)
    holds, detail = _holds(assertion, item, output)
    passed = holds if group == "expected" else not holds
    return AssertionResult(group, assertion.kind, passed, "" if passed else detail, assertion.why)


def run_item(item: GoldenItem, output: str, *, max_words: int = DEFAULT_MAX_WORDS) -> ItemResult:
    """Run the suite checks and the item's own assertions on one output."""
    results = _suite_results(item, output, max_words)
    results += [check_assertion(a, item, output, group="expected") for a in item.expected]
    results += [check_assertion(a, item, output, group="must_not") for a in item.must_not]
    return ItemResult(item.id, tuple(results))


def run_set(
    golden: GoldenSet,
    generate: Callable[[GoldenItem], str],
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    approved_only: bool = False,
) -> SuiteReport:
    """Generate one output per item with `generate` and check it.

    `approved_only=True` runs only the items the owner approved: that is the set a
    signed-off baseline is measured on. The default runs every item, drafts
    included, which is right for wiring checks and wrong for quality claims.
    """
    items = golden.approved if approved_only else golden.items
    return SuiteReport(
        golden.name, tuple(run_item(i, generate(i), max_words=max_words) for i in items)
    )
