"""Pre-commit PII scan for this public repo: emails, phone numbers, Canadian SINs.

Secrets are gitleaks' job; this catches personal data that isn't a secret but must
never be public (CLAUDE.md: no real calendar, prospect or learner data here).
Not caught: SINs written without separators (too many random 9-digit numbers pass
the checksum) and names or addresses. It's a net, not a guarantee.
Standard library only. A line can opt out with a trailing ``pii-ok`` comment, for
deliberate sample data; keep those rare.

Usage: python scripts/pii_scan.py FILE...   (exit 1 on findings)
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
# North American numbers: (514) 555-0123, (514)555-0123, 514-555-0123, +1 514 555 0123
PHONE = re.compile(r"(?<![\w-])(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]\d{4}(?![\w-])")
# Unseparated: 5145550123 or +15145550123. NANP exchanges start 2-9, which keeps
# most other 10-digit numbers (epoch timestamps start with 1) out.
PHONE_DIGITS = re.compile(r"(?<![\w.+-])(?:\+1)?[2-9]\d{2}[2-9]\d{6}(?![\w.-])")
# Anything else with an explicit country code: +33 1 23 45 67 89, +44 20 7946 0958 (pii-ok)
PHONE_INTL = re.compile(r"(?<![\w+])\+(?!1\b)[2-9]\d{0,2}(?:[ .-]?\d{1,4}){3,6}(?![\w-])")
SIN = re.compile(r"(?<![\w-])\d{3}[ -]\d{3}[ -]\d{3}(?![\w-])")

ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "users.noreply.github.com",
)
ALLOWED_EMAILS = {"noreply@anthropic.com", "git@github.com"}
RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost")  # RFC 2606: never a person
FICTIONAL_PHONE = re.compile(r"555[ .-]01\d\d$")  # NANP 555-0100..0199 are fictional
FICTIONAL_DIGITS = [f"55501{n:02d}" for n in range(100)]
OPT_OUT = "pii-ok"
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".svg"}


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _email_allowed(email: str) -> bool:
    email = email.lower()
    domain = email.rsplit("@", 1)[1]
    return (
        email in ALLOWED_EMAILS
        or domain.endswith(RESERVED_TLDS)
        or any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)
    )


def scan_text(text: str) -> Iterator[tuple[int, str, str]]:
    """Yield (line number, kind, match) for each finding."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        if OPT_OUT in line:
            continue
        for m in EMAIL.finditer(line):
            if not _email_allowed(m.group(0)):
                yield lineno, "email", m.group(0)
        phones = [m.group(0) for m in PHONE.finditer(line)]
        phones += [m.group(0) for m in PHONE_DIGITS.finditer(line)]
        phones += [m.group(0) for m in PHONE_INTL.finditer(line) if m.group(0) not in phones]
        for phone in phones:
            if not FICTIONAL_PHONE.search(phone) and not phone.endswith(tuple(FICTIONAL_DIGITS)):
                yield lineno, "phone", phone
        for m in SIN.finditer(line):
            digits = re.sub(r"\D", "", m.group(0))
            if digits[0] not in "08" and _luhn(digits):
                yield lineno, "SIN", m.group(0)


def main(argv: list[str]) -> int:
    found = 0
    for name in argv:
        path = Path(name)
        if path.suffix in SKIP_SUFFIXES or not path.is_file():
            continue
        # errors="replace": a cp1252 export of learner data must not slip past.
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, kind, match in scan_text(text):
            found += 1
            print(f"{name}:{lineno}: possible {kind}: {match}")
    if found:
        print(f"\n{found} possible PII finding(s). Remove them, or mark deliberate sample data")
        print(f"with a '{OPT_OUT}' comment on the line.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
