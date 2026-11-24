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


class _OfflineAdapter:
    """A requests transport that answers every request locally (200, `{}`) and records it."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, request: Any, **kwargs: Any) -> Any:
        import requests

        self.sent.append(str(request.url))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.headers["Content-Type"] = "application/json"
        response.url = str(request.url)
        response.request = request
        return response

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _langsmith_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast tests never reach LangSmith, even with tracing switched on and a fake key: the
    masked tracer's client gets a session whose transport answers locally."""
    try:  # measured_ops isn't installed in measured_evals' standalone CI run
        import requests
        from langsmith import Client

        import measured_ops.tracing as tracing
    except ImportError:
        return

    def offline_client(**kwargs: Any) -> Client:
        client = Client(session=requests.Session(), **kwargs)
        # mounted after construction: the client mounts its own retrying adapter in __init__
        adapter = _OfflineAdapter()
        client.session.mount("https://", adapter)  # type: ignore[arg-type]
        client.session.mount("http://", adapter)  # type: ignore[arg-type]
        return client

    monkeypatch.setattr(tracing, "Client", offline_client)
