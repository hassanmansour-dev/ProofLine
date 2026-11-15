"""guard-v0: deterministic quality, prompt-injection and PII checks (C7, ADR-0018).

A check is `(text, context) -> CheckResult`: a score of 0 or 1 and, on failure, a
short reason. **A reason never contains the matched text**: it names the kind of
problem ("email found"), so a personal value can't leak into scores, logs, traces
or an approval screen. No model, no network: judge models arrive in GROW-14 and the
full injection suite in GROW-15. These are nets, not guarantees.

Context keys read here (all optional):
- `pii_allow`: exact emails or phone numbers that may appear (from private config);
- `max_words`: a positive int;
- `require_citations`: true to require at least one `[S1]`-style citation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True, slots=True)
class CheckResult:
    score: float  # 0.0 or 1.0 in v0
    reason: str | None = None  # set on failure; never quotes the text


PASS = CheckResult(1.0)


def _fail(reason: str) -> CheckResult:
    return CheckResult(0.0, reason)


CheckFn = Callable[[str, Mapping[str, object]], CheckResult]


class Check(NamedTuple):
    name: str
    fn: CheckFn


# --- pii -------------------------------------------------------------------------------

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
# North American: (514) 555-0123, 514-555-0123, +1 514 555 0123, 5145550123
_PHONE_NA = re.compile(r"(?<![\w+-])(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}(?![\w-])")
# Any other explicit country code: +44 20 7946 0958  (pii-ok: Ofcom drama range)
_PHONE_INTL = re.compile(r"(?<![\w+])\+(?!1\b)[2-9]\d{0,2}(?:[ .-]?\d{1,4}){3,6}(?![\w-])")
_SIN = re.compile(r"(?<![\w-])\d{3}[ -]?\d{3}[ -]?\d{3}(?![\w-])")
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_key(s: str) -> str:
    """Digits of a phone number, without the North American "1" prefix, so the same
    number matches whatever its format; another country code stays part of it."""
    d = _digits(s)
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _allowlist(context: Mapping[str, object]) -> tuple[set[str], set[str]]:
    raw = context.get("pii_allow", ())
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise ValueError("context['pii_allow'] must be a list of strings")
    items = list(raw)
    if not all(isinstance(i, str) for i in items):
        raise ValueError("context['pii_allow'] must be a list of strings")
    emails = {i.lower() for i in items if "@" in i}
    phones = {_phone_key(i) for i in items if "@" not in i and _digits(i)}
    return emails, phones


def _pii_matches(text: str) -> list[tuple[int, int, str]]:
    """(start, end, kind) for every email, phone number, SIN and card number, sorted,
    without overlaps. Kinds: "email", "phone", "sin", "card"."""
    found: list[tuple[int, int, str]] = [(*m.span(), "email") for m in _EMAIL.finditer(text)]
    # cards first: their digit runs would otherwise also look like phone numbers
    for m in _CARD.finditer(text):
        d = _digits(m.group(0))
        if 13 <= len(d) <= 19 and _luhn(d):
            found.append((*m.span(), "card"))
    found += [(*m.span(), "phone") for m in _PHONE_NA.finditer(text)]
    found += [(*m.span(), "phone") for m in _PHONE_INTL.finditer(text)]
    found += [(*m.span(), "sin") for m in _SIN.finditer(text) if _luhn(_digits(m.group(0)))]
    order = {"email": 0, "card": 1, "phone": 2, "sin": 3}  # earlier kinds win overlaps
    kept: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(found, key=lambda f: (order[f[2]], f[0])):
        if all(end <= a or start >= b for a, b, _ in kept):
            kept.append((start, end, kind))
    return sorted(kept)


_REASON = {
    "email": "email found",
    "phone": "phone number found",
    "sin": "SIN found",
    "card": "card number found",
}


def pii_check(text: str, context: Mapping[str, object]) -> CheckResult:
    """Emails, phone numbers, SINs and card numbers (Luhn-checked). Fictional values
    count too: anything that leaves the system is checked, unlike the repo scanner."""
    allowed_emails, allowed_phones = _allowlist(context)
    kinds: set[str] = set()
    for start, end, kind in _pii_matches(text):
        value = text[start:end]
        if kind == "email" and value.lower() in allowed_emails:
            continue
        if kind == "phone" and _phone_key(value) in allowed_phones:
            continue
        kinds.add(_REASON[kind])
    return _fail(", ".join(sorted(kinds, key=str.lower))) if kinds else PASS


def redact_pii(text: str) -> str:
    """`text` with every email, phone number, SIN and card number replaced by
    `<email>`, `<phone>`, `<sin>` or `<card>`. For traces and other copies of a text
    that nobody needs verbatim. No allowlist: everything is masked."""
    out: list[str] = []
    pos = 0
    for start, end, kind in _pii_matches(text):
        out.append(text[pos:start])
        out.append(f"<{kind}>")
        pos = end
    out.append(text[pos:])
    return "".join(out)


# --- injection -------------------------------------------------------------------------

_OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}?"
    r"\b(?:previous|prior|above|earlier|all|any|system)\b[^.\n]{0,20}?"
    r"\b(?:instructions?|prompts?|rules|directions)\b",
    re.IGNORECASE,
)
_ROLE_TAG = re.compile(
    r"</?\s*(?:system|assistant|user|human|instructions?|source|research_notes)\s*>"
    r"|^\s*(?:system|assistant|human)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_URL_WITH_QUERY = re.compile(r"https?://[^\s<>()\"']+\?[^\s<>()\"']*=", re.IGNORECASE)


def _has_hidden_chars(text: str) -> bool:
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf":  # format chars: zero-width spaces, bidi overrides, ...
            return True
        if cat == "Cc" and ch not in "\n\r\t":
            return True
    return False


def injection_check(text: str, context: Mapping[str, object]) -> CheckResult:
    """Instruction-like text in an output: overrides, role tags, hidden characters,
    links with query parameters (a way to carry data out). Source links in drafts
    trip the last rule; that only raises an action to approval (ADR-0018)."""
    del context
    if _OVERRIDE.search(text):
        return _fail("instruction override")
    if _ROLE_TAG.search(text):
        return _fail("role or system tag")
    if _has_hidden_chars(text):
        return _fail("invisible or control character")
    if _URL_WITH_QUERY.search(text):
        return _fail("link with query parameters")
    return PASS


# --- quality ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(
    r"\bTODO\b|\bTBD\b|\bFIXME\b|\{\{[^}]*\}\}|\[citation needed\]|\[insert[^\]]*\]|lorem ipsum",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\[[A-Za-z]{0,3}\d{1,4}\]")


def quality_check(text: str, context: Mapping[str, object]) -> CheckResult:
    """Structure only: not empty, within `max_words`, no placeholders, cited if asked."""
    max_words = context.get("max_words")
    if max_words is not None and (
        isinstance(max_words, bool) or not isinstance(max_words, int) or max_words < 1
    ):
        raise ValueError(f"context['max_words'] must be a positive int, got {max_words!r}")
    if not text.strip():
        return _fail("empty")
    if max_words is not None and len(text.split()) > max_words:
        return _fail(f"over {max_words} words")
    if _PLACEHOLDER.search(text):
        return _fail("placeholder left in")
    if context.get("require_citations") and not _CITATION.search(text):
        return _fail("no citations")
    return PASS


DEFAULT_CHECKS: tuple[Check, ...] = (
    Check("quality", quality_check),
    Check("injection", injection_check),
    Check("pii", pii_check),
)
# For short fields that aren't prose (an action's target URL): no quality check.
TARGET_CHECKS: tuple[Check, ...] = (
    Check("injection", injection_check),
    Check("pii", pii_check),
)
