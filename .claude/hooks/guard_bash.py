"""PreToolUse(Bash): block or escalate risky shell commands.

Defense in depth with the deny rules in settings.json: permission rules match
prefixes, this hook catches the same intent anywhere in a compound command.
"""

from __future__ import annotations

import re

from _common import pre_tool_decision, read_input

DENY: list[tuple[str, str]] = [
    (
        r"\bgit\s+push\b[^|;&]*\s(--force(?!-with-lease)|-f)\b",
        "Force-push is blocked. Push normally or ask Hassan.",
    ),
    (r"\bgit\s+commit\b[^|;&]*--no-verify", "--no-verify skips the checks this repo relies on."),
    (
        r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\$HOME)(\s|$)",
        "Recursive delete of / or home is blocked.",
    ),
    (
        r"(^|[\s;&|])(cat|less|more|head|tail|bat|grep|rg|sed|awk|cp|mv|source|\.)\s[^|;&]*\.env(?!\.example)\b",
        ".env holds secrets and is off-limits. Use .env.example for structure.",
    ),
    (r"\b(printenv|env)\b\s*($|\|)", "Dumping the environment would expose API keys."),
    (
        r"(^|[\s;&|])pip3?\s+install\b",
        "Use `uv add <pkg>` (or `uv add --dev`) — never pip directly.",
    ),
    (r"\bpython3?\s+-m\s+pip\s+install\b", "Use `uv add <pkg>` — never pip directly."),
    (r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b", "Piping a download into a shell is blocked."),
]

ASK: list[tuple[str, str]] = [
    (r"\brm\s+-[a-z]*r", "Recursive delete — confirm the path is right."),
    (
        r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\s+\.)",
        "This discards uncommitted work.",
    ),
    (r"\bpytest\b[^|;&]*-m\s+[\"']?live", "Live tests call paid APIs (Anthropic + Tavily)."),
    (r"\bdrop\s+(table|database)\b", "Destructive SQL."),
]


def main() -> None:
    data = read_input()
    command = str(data.get("tool_input", {}).get("command", ""))
    if not command:
        return
    for pattern, reason in DENY:
        if re.search(pattern, command, flags=re.IGNORECASE):
            pre_tool_decision("deny", reason)
    for pattern, reason in ASK:
        if re.search(pattern, command, flags=re.IGNORECASE):
            pre_tool_decision("ask", reason)


if __name__ == "__main__":
    main()
