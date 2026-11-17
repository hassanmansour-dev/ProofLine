"""Inbound screen: instruction-like text in content read from outside (GROW-15, ADR-0021).

`checks.injection_check` looks at what the system is about to send **out**. This
module looks at what comes **in**: a web page, a search result, a tool result,
before it reaches a prompt. The two differ on purpose: a link with query parameters
is normal on a web page and suspicious in a draft, so it isn't flagged here; a
tool-call imitation or a "you are now" line matters far more on the way in.

What a flag means. It is **not** permission to trust unflagged text. Fetched content
is data whether or not it is flagged: it is still wrapped, escaped and never obeyed.
A flag is a signal for the caller to drop the source, lower its weight or show a
human, and it is evidence in a trace of what was attempted.

What it can't do. These are patterns. They catch the plain forms and miss a polite
paraphrase, an encoded payload or a bare claim of authority. The corpus keeps those
misses in view (`today = "passed"` on an attack) instead of hiding them, and
`run_corpus()` reports both rates on that corpus. The corpus is demo data written
for this repo, so its rates describe the corpus and nothing else.

A reason names the kind of problem and never quotes the text.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from measured_evals.checks import _OVERRIDE, _ROLE_TAG, PASS, CheckResult, _has_hidden_chars

_OVERRIDE_FR = re.compile(
    r"\b(?:ignore[zr]?|oublie[zr]?|ne\s+tiens?\s+pas\s+compte)\b[^.\n]{0,30}?"
    r"\b(?:instructions?|consignes?|directives?|règles)\b[^.\n]{0,20}?"
    r"\b(?:précédentes?|antérieures?|ci-dessus|du\s+système)\b",
    re.IGNORECASE,
)
_ROLE_CHANGE = re.compile(
    r"\b(?:from\s+now\s+on|henceforth)\b[^.\n]{0,20}?\byou\s+(?:are|will|must)\b"
    r"|\byou\s+are\s+now\b"
    r"|^\s*new\s+instructions?\s*:",
    re.IGNORECASE | re.MULTILINE,
)
# an image whose address carries a query: rendering it sends that query to the host
_MARKDOWN_IMAGE_QUERY = re.compile(r"!\[[^\]]*\]\(\s*https?://[^\s)]+\?[^\s)]*=", re.IGNORECASE)
_TOOL_CALL = re.compile(
    r"\{\s*\"(?:tool|tool_name|function|function_call|tool_use|name)\"\s*:\s*\"[^\"]+\"\s*,"
    r"\s*\"(?:arguments|args|parameters|input)\"\s*:",
    re.IGNORECASE,
)

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction override", _OVERRIDE),
    ("instruction override", _OVERRIDE_FR),
    ("role or system tag", _ROLE_TAG),
    ("role change", _ROLE_CHANGE),
    ("image link that carries data out", _MARKDOWN_IMAGE_QUERY),
    ("tool call imitation", _TOOL_CALL),
)


def inbound_check(text: str, context: Mapping[str, object]) -> CheckResult:
    """Score 0 with a reason when `text` carries instruction-like content, else 1."""
    del context
    for reason, pattern in _RULES:
        if pattern.search(text):
            return CheckResult(0.0, reason)
    if _has_hidden_chars(text):
        return CheckResult(0.0, "invisible or control character")
    return PASS


Label = Literal["attack", "benign"]
Outcome = Literal["flagged", "passed"]


@dataclass(frozen=True, slots=True)
class InboundCase:
    id: str
    label: Label
    category: str
    today: Outcome
    text: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: InboundCase
    outcome: Outcome
    reason: str | None

    @property
    def as_documented(self) -> bool:
        return self.outcome == self.case.today


@dataclass(frozen=True, slots=True)
class CorpusReport:
    name: str
    results: tuple[CaseResult, ...]

    def _count(self, label: Label, outcome: Outcome) -> int:
        return sum(1 for r in self.results if r.case.label == label and r.outcome == outcome)

    @property
    def attacks(self) -> int:
        return sum(1 for r in self.results if r.case.label == "attack")

    @property
    def benign(self) -> int:
        return len(self.results) - self.attacks

    @property
    def attacks_flagged(self) -> int:
        return self._count("attack", "flagged")

    @property
    def benign_flagged(self) -> int:
        return self._count("benign", "flagged")

    @property
    def known_misses(self) -> tuple[str, ...]:
        return tuple(
            r.case.id for r in self.results if r.case.label == "attack" and r.outcome == "passed"
        )

    @property
    def undocumented(self) -> tuple[str, ...]:
        """Cases whose outcome differs from the corpus's `today` column."""
        return tuple(r.case.id for r in self.results if not r.as_documented)


def parse_corpus(document: Mapping[str, object]) -> tuple[str, tuple[InboundCase, ...]]:
    name = document.get("name")
    raw = document.get("cases")
    if not isinstance(name, str) or not isinstance(raw, list) or not raw:
        raise ValueError("an inbound corpus needs a name and a non-empty list of cases")
    cases: list[InboundCase] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, Mapping):
            raise ValueError("each case must be a table")
        fields = {k: row.get(k) for k in ("id", "label", "category", "today", "text")}
        if not all(isinstance(v, str) and v for v in fields.values()):
            raise ValueError(f"case {fields['id']!r}: every field must be a non-empty string")
        label, today, case_id = fields["label"], fields["today"], str(fields["id"])
        if label not in ("attack", "benign") or today not in ("flagged", "passed"):
            raise ValueError(f"case {case_id!r}: bad label or today value")
        if case_id in seen:
            raise ValueError(f"case {case_id!r} appears twice")
        seen.add(case_id)
        cases.append(
            InboundCase(
                id=case_id,
                label=label,
                category=str(fields["category"]),
                today=today,
                text=str(fields["text"]),
            )
        )
    return name, tuple(cases)


def load_corpus() -> tuple[str, tuple[InboundCase, ...]]:
    """The bundled demo corpus, `data/injection/inbound_v0.toml`."""
    path = resources.files("measured_evals") / "data" / "injection" / "inbound_v0.toml"
    return parse_corpus(tomllib.loads(path.read_text(encoding="utf-8")))


def run_corpus() -> CorpusReport:
    name, cases = load_corpus()
    results = []
    for case in cases:
        result = inbound_check(case.text, {})
        results.append(
            CaseResult(case, "passed" if result.score == 1.0 else "flagged", result.reason)
        )
    return CorpusReport(name=name, results=tuple(results))


def report_lines(report: CorpusReport) -> list[str]:
    return [
        f"inbound corpus: {report.name} (demo data written for this repo; not a benchmark)",
        f"  attacks flagged: {report.attacks_flagged} of {report.attacks}",
        f"  benign flagged (false alarms): {report.benign_flagged} of {report.benign}",
        f"  known misses, kept on purpose: {list(report.known_misses)}",
        "these counts describe this corpus only. Unflagged text is still untrusted data.",
    ]
