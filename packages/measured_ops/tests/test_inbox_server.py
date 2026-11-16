"""`serve(agents)`: the one way to run the inbox (C4; shared by `proofline inbox` in C8)."""

from __future__ import annotations

from typing import Any

import pytest

from measured_ops.echo import EchoAgent
from measured_ops.inbox import server
from measured_ops.inbox.auth import hash_passphrase


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("MEASURED_OPS_INBOX_PASSPHRASE_HASH", hash_passphrase("a" * 12, n=2**10))
    monkeypatch.setenv("MEASURED_OPS_INBOX_SESSION_SECRET", "k" * 40)
    monkeypatch.setenv("MEASURED_OPS_INBOX_CHECKPOINTER_URL", f"sqlite:///{tmp_path}/cp.db")


def test_serve_binds_localhost_one_worker_with_the_given_agents(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        seen.update(kwargs, app=app)

    monkeypatch.setattr("uvicorn.run", fake_run)
    built: dict[str, Any] = {}
    real = server.create_inbox_app

    def spy(settings: Any, agents: Any, policy: Any, **kw: Any) -> Any:
        built.update(agents=[a.name for a in agents], **kw)
        return real(settings, agents, policy, **kw)

    monkeypatch.setattr(server, "create_inbox_app", spy)

    assert server.serve([EchoAgent()], eval_context={"pii_allow": []}) == 0

    assert (seen["host"], seen["workers"]) == ("127.0.0.1", 1)
    assert built["agents"] == ["echo"]
    assert built["eval_context"] == {"pii_allow": []}


def test_serve_refuses_to_start_without_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MEASURED_OPS_INBOX_PASSPHRASE_HASH", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("must not start"))

    assert server.serve([EchoAgent()]) == 2
    assert "PASSPHRASE_HASH" in capsys.readouterr().err
