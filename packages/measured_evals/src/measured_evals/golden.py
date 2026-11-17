"""Golden sets: typed, validated topics with checkable expectations (GROW-13).

A golden set is a small list of inputs with what a good output must do and must not
do, written down *before* looking at any output. It turns "this looks fine" into a
check that gives the same answer tomorrow.

The format is generic: an item is a topic, an audience, fixture sources and two
lists of assertions. Nothing here knows an app's types (ADR-0012). The files are
TOML (read with the standard library, so the package keeps zero runtime
dependencies) and every field is validated on load: an unknown key, an unknown
assertion kind or a citation of a source the item doesn't have is an error, never
a silent skip.

**Review status.** Every item carries `status`: `needs_eval` (drafted, not yet
reviewed by the owner), `approved` (needs `approved_by`; `approved_on`, an ISO date,
is optional) or `rejected`. Only approved items count as a golden set;
`GoldenSet.approved` returns just those. Drafts are useful for wiring and for
teaching, not for claiming quality. Approval is about the cases, not about any
output: an approved set with no real run against it has measured nothing.

Assertion kinds. `expected` lists what the output must do; `must_not` lists
behaviours that must be absent.

| kind | tier | params | holds when |
|---|---|---|---|
| `max_words` | deterministic | `value` | the output has at most `value` words |
| `min_citations` | deterministic | `value` | at least `value` distinct source ids are cited |
| `citations_resolve` | deterministic | none | every `[S1]`-style citation names one of the item's sources |
| `cites` | deterministic | `sources` | each listed source id is cited at least once |
| `mentions_any` | deterministic | `terms` | at least one term appears (case-insensitive) |
| `mentions_all` | deterministic | `terms` | every term appears (case-insensitive) |
| `contains` | deterministic | `terms` | (`must_not` only) the behaviour is present if any term appears |
| `uncited_numbers` | deterministic | none | (`must_not` only) present if a sentence has a number and no citation |
| `uncited_sentences` | deterministic | none | (`must_not` only) present if a body sentence has no citation |
| `judge` | judge | `criterion` | a model or a human answers the yes/no criterion |

Deterministic kinds are evaluated by `measured_evals.suites` (GROW-14); `judge`
kinds need a model or a person and never run in the keyless CI tier.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Literal, cast

SCHEMA_VERSION = 1
DEFAULT_SET = "content_v0"

Status = Literal["needs_eval", "approved", "rejected"]
Difficulty = Literal["easy", "medium", "hard"]
ItemKind = Literal["standard", "injection_bait", "unanswerable"]
Tier = Literal["deterministic", "judge"]

STATUSES: tuple[str, ...] = ("needs_eval", "approved", "rejected")
DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")
ITEM_KINDS: tuple[str, ...] = ("standard", "injection_bait", "unanswerable")

# kind -> (required param or None, allowed in expected, allowed in must_not)
_KINDS: dict[str, tuple[str | None, bool, bool]] = {
    "max_words": ("value", True, False),
    "min_citations": ("value", True, False),
    "citations_resolve": (None, True, False),
    "cites": ("sources", True, False),
    "mentions_any": ("terms", True, False),
    "mentions_all": ("terms", True, False),
    "contains": ("terms", False, True),
    "uncited_numbers": (None, False, True),
    "uncited_sentences": (None, False, True),
    "judge": ("criterion", True, True),
}
ASSERTION_KINDS: tuple[str, ...] = tuple(_KINDS)

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SOURCE_ID = re.compile(r"^S[1-9]\d{0,2}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TAG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class GoldenSetError(ValueError):
    """A golden-set file that doesn't match the format. The message names the item."""


@dataclass(frozen=True, slots=True)
class Assertion:
    """One checkable statement about an output. `why` is shown to learners."""

    kind: str
    why: str
    value: int | None = None
    terms: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    criterion: str | None = None

    @property
    def tier(self) -> Tier:
        return "judge" if self.kind == "judge" else "deterministic"


@dataclass(frozen=True, slots=True)
class GoldenSource:
    """A fixture source the writer may cite. Written for this set: demo data, not a quote."""

    id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class GoldenItem:
    id: str
    kind: ItemKind
    status: Status
    topic: str
    audience: str
    difficulty: Difficulty
    tags: tuple[str, ...]
    lesson: str
    sources: tuple[GoldenSource, ...]
    expected: tuple[Assertion, ...]
    must_not: tuple[Assertion, ...]
    approved_by: str | None = None
    approved_on: str | None = None  # ISO date the owner approved it, when recorded

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.sources)


@dataclass(frozen=True, slots=True)
class GoldenSet:
    name: str
    description: str
    items: tuple[GoldenItem, ...]

    @property
    def approved(self) -> tuple[GoldenItem, ...]:
        """Only the items the owner approved. A draft set returns nothing here."""
        return tuple(i for i in self.items if i.status == "approved")

    def by_id(self, item_id: str) -> GoldenItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(item_id)

    def status_counts(self) -> dict[str, int]:
        return {s: sum(1 for i in self.items if i.status == s) for s in STATUSES}


# --- parsing ---------------------------------------------------------------------------


def _fail(where: str, message: str) -> GoldenSetError:
    return GoldenSetError(f"{where}: {message}")


def _table(raw: object, where: str, allowed: set[str]) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise _fail(where, "must be a table")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(where, f"unknown keys {unknown}")
    return cast(Mapping[str, object], raw)


def _text(raw: Mapping[str, object], key: str, where: str, *, max_len: int = 600) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, f"{key!r} must be non-empty text")
    if len(value) > max_len:
        raise _fail(where, f"{key!r} is over {max_len} characters")
    return value.strip()


def _choice(raw: Mapping[str, object], key: str, where: str, options: tuple[str, ...]) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or value not in options:
        raise _fail(where, f"{key!r} must be one of {list(options)}, got {value!r}")
    return value


def _strings(value: object, where: str, key: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v.strip() for v in value)
    ):
        raise _fail(where, f"{key!r} must be a non-empty list of non-empty strings")
    items = tuple(cast(list[str], value))
    if len(set(items)) != len(items):
        raise _fail(where, f"{key!r} has duplicates")
    return items


def _tables(raw: Mapping[str, object], key: str, where: str) -> list[object]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise _fail(where, f"{key!r} must be an array of tables")
    return cast(list[object], value)


def _assertion(raw: object, where: str, *, in_must_not: bool, source_ids: set[str]) -> Assertion:
    table = _table(raw, where, {"kind", "why", "value", "terms", "sources", "criterion"})
    kind = _choice(table, "kind", where, ASSERTION_KINDS)
    param, in_expected_ok, in_must_not_ok = _KINDS[kind]
    if in_must_not and not in_must_not_ok:
        raise _fail(where, f"{kind!r} belongs in 'expected', not 'must_not'")
    if not in_must_not and not in_expected_ok:
        raise _fail(where, f"{kind!r} belongs in 'must_not', not 'expected'")
    extra = sorted(set(table) - {"kind", "why"} - ({param} if param else set()))
    if extra:
        raise _fail(where, f"{kind!r} takes {param or 'no parameter'}, got {extra}")
    why = _text(table, "why", where, max_len=300)
    value: int | None = None
    terms: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    criterion: str | None = None
    if param == "value":
        number = table.get("value")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise _fail(where, f"'value' must be a positive integer, got {number!r}")
        value = number
    elif param == "terms":
        terms = _strings(table.get("terms"), where, "terms")
    elif param == "sources":
        sources = _strings(table.get("sources"), where, "sources")
        missing = sorted(set(sources) - source_ids)
        if missing:
            raise _fail(where, f"cites sources the item doesn't have: {missing}")
    elif param == "criterion":
        criterion = _text(table, "criterion", where, max_len=300)
    return Assertion(kind, why, value, terms, sources, criterion)


def _source(raw: object, where: str) -> GoldenSource:
    table = _table(raw, where, {"id", "title", "text"})
    source_id = _text(table, "id", where)
    if not _SOURCE_ID.match(source_id):
        raise _fail(where, f"source id must look like S1, got {source_id!r}")
    return GoldenSource(
        source_id, _text(table, "title", where), _text(table, "text", where, max_len=1200)
    )


_ITEM_KEYS = {
    "id",
    "kind",
    "status",
    "approved_by",
    "approved_on",
    "topic",
    "audience",
    "difficulty",
    "tags",
    "lesson",
    "sources",
    "expected",
    "must_not",
}


def _item(raw: object, index: int) -> GoldenItem:
    where = f"items[{index}]"
    table = _table(raw, where, _ITEM_KEYS)
    item_id = _text(table, "id", where)
    if not _ID.match(item_id):
        raise _fail(where, f"id must be lowercase words joined by hyphens, got {item_id!r}")
    where = f"item {item_id}"
    status = cast(Status, _choice(table, "status", where, STATUSES))
    approved_by = table.get("approved_by")
    if approved_by is not None and (not isinstance(approved_by, str) or not approved_by.strip()):
        raise _fail(where, "'approved_by' must be non-empty text when present")
    if status == "approved" and approved_by is None:
        raise _fail(where, "an approved item needs 'approved_by'")
    if status != "approved" and approved_by is not None:
        raise _fail(where, "'approved_by' is only for approved items")
    approved_on = table.get("approved_on")
    if approved_on is not None:
        if status != "approved":
            raise _fail(where, "'approved_on' is only for approved items")
        if not isinstance(approved_on, str) or not _ISO_DATE.match(approved_on):
            raise _fail(where, "'approved_on' must be a date like 2026-01-31")
        try:
            date.fromisoformat(approved_on)
        except ValueError as exc:
            raise _fail(where, "'approved_on' is not a real date") from exc
    tags = _strings(table.get("tags"), where, "tags")
    for tag in tags:
        if not _TAG.match(tag):
            raise _fail(where, f"tag must be lowercase words joined by hyphens, got {tag!r}")
    sources = tuple(
        _source(s, f"{where} sources[{n}]") for n, s in enumerate(_tables(table, "sources", where))
    )
    ids = [s.id for s in sources]
    if len(set(ids)) != len(ids):
        raise _fail(where, "duplicate source ids")
    source_ids = set(ids)
    expected = tuple(
        _assertion(a, f"{where} expected[{n}]", in_must_not=False, source_ids=source_ids)
        for n, a in enumerate(_tables(table, "expected", where))
    )
    must_not = tuple(
        _assertion(a, f"{where} must_not[{n}]", in_must_not=True, source_ids=source_ids)
        for n, a in enumerate(_tables(table, "must_not", where))
    )
    if not expected:
        raise _fail(where, "needs at least one 'expected' assertion")
    if not must_not:
        raise _fail(where, "needs at least one 'must_not' assertion")
    return GoldenItem(
        id=item_id,
        kind=cast(ItemKind, _choice(table, "kind", where, ITEM_KINDS)),
        status=status,
        topic=_text(table, "topic", where),
        audience=_text(table, "audience", where),
        difficulty=cast(Difficulty, _choice(table, "difficulty", where, DIFFICULTIES)),
        tags=tags,
        lesson=_text(table, "lesson", where),
        sources=sources,
        expected=expected,
        must_not=must_not,
        approved_by=approved_by.strip() if isinstance(approved_by, str) else None,
        approved_on=approved_on,
    )


def parse_golden_set(data: Mapping[str, object]) -> GoldenSet:
    """Validate a parsed TOML document and return the typed set."""
    top = _table(dict(data), "golden set", {"schema_version", "name", "description", "items"})
    if top.get("schema_version") != SCHEMA_VERSION:
        raise _fail("golden set", f"schema_version must be {SCHEMA_VERSION}")
    name = _text(top, "name", "golden set")
    items = tuple(_item(raw, n) for n, raw in enumerate(_tables(top, "items", "golden set")))
    if not items:
        raise _fail("golden set", "has no items")
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise _fail("golden set", f"duplicate item id {item.id!r}")
        seen.add(item.id)
    return GoldenSet(name, _text(top, "description", "golden set"), items)


def load_golden_set(path: str | Path | None = None) -> GoldenSet:
    """Load and validate a golden set. With no path, the bundled `content_v0`."""
    if path is None:
        text = (
            resources.files("measured_evals")
            .joinpath("data", "golden", f"{DEFAULT_SET}.toml")
            .read_text(encoding="utf-8")
        )
    else:
        text = Path(path).read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise GoldenSetError(f"not valid TOML: {exc}") from exc
    return parse_golden_set(data)
