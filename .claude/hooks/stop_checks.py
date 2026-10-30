"""Stop: if Python files changed, run the fast suite + ruff before Claude finishes.

Failures are handed back once (exit 2 makes Claude continue). If this hook already
forced a continuation (`stop_hook_active`), it reports but doesn't block again,
so it can never loop. Disable for a session with PROOFLINE_SKIP_STOP_CHECKS=1.
"""

from __future__ import annotations

import os

from _common import feedback_to_claude, read_input, run, uv_available


def python_changed() -> bool:
    tracked = run(["git", "diff", "--name-only", "HEAD"], timeout=10)
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], timeout=10)
    if tracked.returncode != 0:  # no commits yet → check untracked only
        names = untracked.stdout.splitlines()
    else:
        names = tracked.stdout.splitlines() + untracked.stdout.splitlines()
    return any(n.endswith(".py") for n in names)


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def main() -> None:
    data = read_input()
    if os.environ.get("PROOFLINE_SKIP_STOP_CHECKS") == "1":
        return
    if data.get("stop_hook_active"):
        return
    if not uv_available() or not python_changed():
        return

    problems: list[str] = []
    lint = run(
        ["uv", "run", "--quiet", "ruff", "check", "--output-format", "concise", "."], timeout=60
    )
    if lint.returncode != 0:
        problems.append("ruff:\n" + tail(lint.stdout or lint.stderr, 20))

    tests = run(
        [
            "uv",
            "run",
            "--quiet",
            "pytest",
            "-m",
            "not live",
            "-q",
            "-x",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        timeout=240,
    )
    if tests.returncode not in (0, 5):  # 5 = no tests collected
        problems.append("pytest (fast suite):\n" + tail(tests.stdout + tests.stderr))

    if problems:
        feedback_to_claude(
            "Python files changed and the fast checks are failing. Fix these before finishing, "
            "or explain to Hassan why they're expected to fail:\n\n" + "\n\n".join(problems)
        )


if __name__ == "__main__":
    main()
