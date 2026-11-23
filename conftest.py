"""Repo-wide test guard (C8): tests never write the real runtime data.

Each package's conftest points the judge log and the posted marks at `tmp_path`. This
guard makes that enforced, not promised: inside the test process, any write to the
real `.data/judge_log.jsonl` or `.data/posted.jsonl` (which may be Hassan's) raises at
once. It guards the writers, not the files, because a running inbox may legitimately
write those files while tests run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent
GUARDED = {(ROOT / ".data" / "judge_log.jsonl"), (ROOT / ".data" / "posted.jsonl")}


def _refuse_real(write: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(self.path).resolve() in GUARDED or (
            not Path(self.path).is_absolute() and (Path.cwd() / self.path).resolve() in GUARDED
        ):
            raise AssertionError(
                f"a test tried to write real runtime data {self.path}; use tmp_path"
            )
        return write(self, *args, **kwargs)

    return guarded


@pytest.fixture(autouse=True, scope="session")
def _no_writes_to_real_runtime_data() -> Iterator[None]:
    patches: list[tuple[type, str, Any]] = []
    try:  # measured_ops isn't installed in measured_evals' standalone CI run
        from measured_ops.judge_log import JsonlJudgeLog
        from measured_ops.posted import JsonlPostedLog
    except ImportError:
        yield
        return
    for cls, name in ((JsonlJudgeLog, "_append"), (JsonlPostedLog, "mark")):
        original = getattr(cls, name)
        patches.append((cls, name, original))
        setattr(cls, name, _refuse_real(original))
    try:
        yield
    finally:
        for cls, name, original in patches:
            setattr(cls, name, original)


@pytest.fixture(autouse=True)
def _kill_switch_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every supervisor honours the kill switch (C10, ADR-0023); tests use their own, so a
    switch Hassan engaged in `.data/` never fails a test run, and no test can engage his."""
    monkeypatch.setenv("MEASURED_OPS_KILL_SWITCH_PATH", str(tmp_path / "kill_switch"))
