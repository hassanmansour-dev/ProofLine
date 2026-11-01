"""PostToolUse(Edit|Write|MultiEdit).

- *.py  → ruff format + ruff check --fix; report what's left to Claude.
- prompts/*.md → if the body changed but `version:` didn't, tell Claude to bump it
  and add a CHANGELOG line (ADR-0010).
Exit 2 on PostToolUse doesn't undo the edit; it hands stderr to Claude.
"""

from __future__ import annotations

import re
from pathlib import Path

from _common import feedback_to_claude, project_dir, read_input, run, uv_available

VERSION_RE = re.compile(r"^version:\s*(\S+)", re.MULTILINE)


def check_python(path: Path) -> str | None:
    if not uv_available():
        return None
    run(["uv", "run", "--quiet", "ruff", "format", str(path)], timeout=30)
    result = run(
        ["uv", "run", "--quiet", "ruff", "check", "--fix", "--output-format", "concise", str(path)],
        timeout=30,
    )
    if result.returncode != 0:
        issues = (result.stdout or result.stderr).strip()
        return f"ruff found issues it could not auto-fix in {path.name}:\n{issues}\nFix them before continuing."
    return None


def check_prompt(path: Path, rel: str) -> str | None:
    head = run(["git", "show", f"HEAD:{rel}"], timeout=10)
    if head.returncode != 0:
        return None  # new file or no commits yet
    old, new = head.stdout, path.read_text(encoding="utf-8")
    if old == new:
        return None
    old_v = VERSION_RE.search(old)
    new_v = VERSION_RE.search(new)
    if old_v and new_v and old_v.group(1) == new_v.group(1):
        return (
            f"You changed {rel} but its version is still {new_v.group(1)}. "
            "Bump `version:` (patch=wording, minor=behavior, major=contract) and add a line "
            "to packages/proofline/src/proofline/prompts/CHANGELOG.md (version, what, why). See docs/PROMPTING.md."
        )
    return None


def main() -> None:
    data = read_input()
    file_path = data.get("tool_input", {}).get("file_path")
    if not file_path:
        return
    path = Path(file_path)
    if not path.exists():
        return
    try:
        rel = path.resolve().relative_to(project_dir()).as_posix()
    except ValueError:
        return

    message = None
    if path.suffix == ".py":
        message = check_python(path)
    elif (
        rel.startswith("packages/proofline/src/proofline/prompts/")
        and path.suffix == ".md"
        and path.name != "CHANGELOG.md"
    ):
        message = check_prompt(path, rel)

    if message:
        feedback_to_claude(message)


if __name__ == "__main__":
    main()
