"""Repo-wide test guard (C8): tests never touch the real runtime data.

Each package's conftest points the judge log and the posted marks at `tmp_path`. This
sentinel makes that enforced, not promised: if a test run creates, changes or deletes
`.data/judge_log.jsonl` or `.data/posted.jsonl` (which may be Hassan's), the run fails.
It only reads their size and modification time, never their contents.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
GUARDED = (ROOT / ".data" / "judge_log.jsonl", ROOT / ".data" / "posted.jsonl")


def _snapshot() -> dict[Path, tuple[int, int] | None]:
    out: dict[Path, tuple[int, int] | None] = {}
    for path in GUARDED:
        try:
            stat = path.stat()
        except FileNotFoundError:
            out[path] = None
        else:
            out[path] = (stat.st_size, stat.st_mtime_ns)
    return out


@pytest.fixture(autouse=True, scope="session")
def _real_runtime_data_untouched() -> Iterator[None]:
    before = _snapshot()
    yield
    after = _snapshot()
    changed = [str(p.relative_to(ROOT)) for p in GUARDED if before[p] != after[p]]
    if changed:
        pytest.fail(f"tests changed real runtime data: {changed}; use tmp_path")
