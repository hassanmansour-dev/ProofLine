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


# Runtime data: judge log, posted marks, checkpoints (.data/) and drafts (outputs/).
# It may be Hassan's, so no command may delete or truncate it (C8: an assumed test
# artifact was his real judge log). Reading, listing and `>>` appends stay allowed.
DATA_REASON = (
    "Files under .data/ and outputs/ are runtime data (judge log, posted marks, "
    "checkpoints, drafts) and may be Hassan's: never delete, move or truncate them. "
    "Ask him."
)
_DATA = r"(?:^|[\s'\"=(/.])(?:\.data|outputs)(?=/|\s|$|['\")])"
_SEGMENTS = re.compile(r"\|\||&&|[;|&\n]")
_DESTRUCTIVE_CMD = re.compile(r"(?:^|/)(?:rm|unlink|shred|truncate|rmdir|mv)$")
_PY_DESTRUCTIVE = re.compile(
    r"\b(?:remove|unlink|rmtree|rmdir|truncate)\s*\(|\bopen\s*\([^)]*['\"]w|write_text\s*\(",
)


_PY_INLINE = re.compile(r"\bpython3?\b[^\n]*?\s-c\s+(['\"])(.*?)\1", re.DOTALL)
_PY_HEREDOC = re.compile(r"\bpython3?\b[^\n]*?<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n\1\b", re.DOTALL)


def _python_snippets(command: str) -> list[str]:
    """The code of inline Python: `-c '...'` arguments and heredoc bodies."""
    return [m.group(2) for m in _PY_INLINE.finditer(command)] + [
        m.group(2) for m in _PY_HEREDOC.finditer(command)
    ]


def _words(segment: str) -> list[str]:
    return [w for w in re.split(r"\s+", segment.strip()) if w]


def touches_runtime_data(command: str) -> bool:
    """True if `command` would delete, move or truncate anything under .data/ or outputs/."""
    if not re.search(_DATA, command):
        return False
    # inline Python (-c or heredoc) that deletes or overwrites runtime data: the path
    # and the call must both be in the Python code itself
    for code in _python_snippets(command):
        if re.search(_DATA, code) and _PY_DESTRUCTIVE.search(code):
            return True
    # `>` or `>|` (not `>>`) onto runtime data truncates it; checked before splitting
    # on `|`, which would cut `>|` in two
    if re.search(r"(?<!>)>\|?\s*[\"']?(?:\S*/)?(?:\.data|outputs)/", command):
        return True
    for segment in _SEGMENTS.split(command):
        if not re.search(_DATA, segment):
            # a redirect target may be in this segment only
            continue
        words = _words(segment)
        if not words:
            continue
        verb = words[0]
        if verb in ("sudo", "command", "env"):
            words = words[1:] or [""]
            verb = words[0]
        if _DESTRUCTIVE_CMD.search(verb):
            return True
        if verb.endswith("find") and re.search(r"\s-(delete|exec\s+(/\S*/)?rm)\b", segment):
            return True
        if verb.endswith("cp") and re.search(_DATA, words[-1]):
            return True
        if verb.endswith("dd") and re.search(r"\bof=\S*(\.data|outputs)", segment):
            return True
        if verb.endswith("tee") and not re.search(r"\s-(a|-append)\b", segment):
            return True
        if verb.endswith("sqlite3") and re.search(
            r"\b(delete|drop|vacuum\s+into)\b", segment, re.I
        ):
            return True
    return False


def main() -> None:
    data = read_input()
    command = str(data.get("tool_input", {}).get("command", ""))
    if not command:
        return
    # `git clean` as a command (start of a segment), not words in a commit message
    git_clean_x = r"(?:^|[;&|\n]|&&|\|\|)\s*git\s+clean\b[^|;&\n]*\s-[a-z]*[xX]"
    if touches_runtime_data(command) or re.search(git_clean_x, command):
        pre_tool_decision(
            "deny",
            DATA_REASON
            if "clean" not in command
            else "git clean -x/-X deletes ignored runtime data (.data/, outputs/): "
            "never run it. Ask Hassan.",
        )
    for pattern, reason in DENY:
        if re.search(pattern, command, flags=re.IGNORECASE):
            pre_tool_decision("deny", reason)
    for pattern, reason in ASK:
        if re.search(pattern, command, flags=re.IGNORECASE):
            pre_tool_decision("ask", reason)


if __name__ == "__main__":
    main()
