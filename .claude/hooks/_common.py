"""Shared helpers for Claude Code hooks. Stdlib only — hooks run before `uv sync`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def read_input() -> dict[str, Any]:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=project_dir(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def pre_tool_decision(decision: str, reason: str) -> None:
    """Emit a PreToolUse permission decision ("allow" | "deny" | "ask") and exit 0."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def feedback_to_claude(message: str) -> None:
    """Exit 2: stderr is shown to Claude (blocks on Pre/Stop, informs on Post)."""
    print(message, file=sys.stderr)
    sys.exit(2)


def uv_available() -> bool:
    return run(["uv", "--version"], timeout=10).returncode == 0
