"""Shared by the e2e tests (C2 supervisor, C4 inbox): pause a run in another process.

The child (ops_e2e_child.py) runs task → echo agent → pause at the gate and exits, so
whatever resumes the run afterwards can only read it from the checkpoint.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CHILD = Path(__file__).with_name("ops_e2e_child.py")


def pause_in_another_process(url: str, thread_id: str) -> dict[str, Any]:
    """Run the child; return the gate's interrupt payload it printed."""
    proc = subprocess.run(
        [sys.executable, str(CHILD), url, thread_id],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return payload
