"""SessionStart: orient Claude — current week, branch, working tree, recent commits.

Plain stdout from a SessionStart hook is added to Claude's context.
"""

from __future__ import annotations

import re

from _common import project_dir, read_input, run


def current_week() -> str:
    plan = project_dir() / "docs" / "PLAN.md"
    if not plan.exists():
        return "unknown (docs/PLAN.md missing)"
    for line in plan.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") and "◀ CURRENT" in line:
            return re.sub(r"^#+\s*", "", line).replace("◀ CURRENT", "").strip()
    return "none marked — ask Hassan which week we're in"


def git(*args: str) -> str:
    result = run(["git", *args], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> None:
    read_input()
    branch = git("branch", "--show-current") or "(no git repo yet)"
    status = git("status", "--short")
    status_lines = status.splitlines()
    log = git("log", "--oneline", "-5")

    print("## Session context (from .claude/hooks/session_start.py)")
    print(f"- Current plan week: **{current_week()}** — scope is docs/PLAN.md for that week.")
    print("- Phase 1 (weeks 1-4) scope rule: no multi-agent, no supervisor, no MCP inside the app.")
    print(
        "- Guardrail: own time and equipment only; no employer or client code, data or names. Repo goes public at launch."
    )
    print(f"- Branch: {branch}")
    if status_lines:
        shown = "\n".join(status_lines[:20])
        more = f"\n  … {len(status_lines) - 20} more" if len(status_lines) > 20 else ""
        print(f"- Uncommitted changes:\n```\n{shown}{more}\n```")
    else:
        print("- Working tree clean.")
    if log:
        print(f"- Recent commits:\n```\n{log}\n```")


if __name__ == "__main__":
    main()
