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
def _no_developer_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shell that exports these (to run the inbox) must not change what tests do: no
    real Postgres logs, no real budget table, no real MCP servers. A test that needs one
    sets it itself."""
    for name in ("MEASURED_OPS_LOGS_URL", "MEASURED_OPS_BUDGET_PATH", "MEASURED_OPS_TOOLS_CONFIG"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _postgres_logs_only_in_the_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Postgres judge log and posted marks open only against the test database
    (MEASURED_OPS_TEST_DATABASE_URL); any other Postgres URL is refused before a
    connection is made."""
    try:  # measured_ops isn't installed in measured_evals' standalone CI run
        from measured_ops import pg_logs
    except ImportError:
        return
    import os

    original = pg_logs._Pg.__init__
    test_db = os.environ.get("MEASURED_OPS_TEST_DATABASE_URL", "")

    def guarded(self: Any, url: str, *args: Any, **kwargs: Any) -> None:
        is_pg = url.startswith(("postgresql://", "postgres://"))
        if is_pg and not (test_db and url.startswith(test_db)):
            raise AssertionError("a test tried to open real Postgres logs; use the test database")
        original(self, url, *args, **kwargs)

    monkeypatch.setattr(pg_logs._Pg, "__init__", guarded)


@pytest.fixture(autouse=True)
def _kill_switch_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every supervisor honours the kill switch (C10, ADR-0023); tests use their own, so a
    switch Hassan engaged in `.data/` never fails a test run, and no test can engage his."""
    monkeypatch.setenv("MEASURED_OPS_KILL_SWITCH_PATH", str(tmp_path / "kill_switch"))


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
