"""Inbox settings from the environment (ADR-0017). No secrets, no start: there is no
default passphrase or session secret. Real values live in measured-ops-private or the
local env file, never in this repo."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from measured_ops.inbox.auth import hash_params_usable, is_passphrase_hash

PREFIX = "MEASURED_OPS_INBOX_"
MIN_SECRET_CHARS = 32


class InboxConfigError(RuntimeError):
    """The inbox can't start safely with this configuration."""


@dataclass(frozen=True)
class InboxSettings:
    passphrase_hash: str = field(repr=False)
    session_secret: str = field(repr=False)
    checkpointer_url: str = "sqlite:///.data/ops_checkpoints.db"
    lock_path: Path = Path(".data/inbox.lock")
    port: int = 8765
    session_hours: int = 12

    def check(self) -> None:
        """Raise InboxConfigError unless the secrets are present and usable."""
        if not self.passphrase_hash:
            raise InboxConfigError(
                f"{PREFIX}PASSPHRASE_HASH is not set: run `python -m measured_ops.inbox hash`"
            )
        if not is_passphrase_hash(self.passphrase_hash):
            raise InboxConfigError(
                f"{PREFIX}PASSPHRASE_HASH is not a passphrase hash: run "
                "`python -m measured_ops.inbox hash` and store its output"
            )
        if not hash_params_usable(self.passphrase_hash):
            raise InboxConfigError(
                f"{PREFIX}PASSPHRASE_HASH can't be checked: its scrypt parameters are invalid "
                "or need more memory than allowed; make a new one with "
                "`python -m measured_ops.inbox hash`"
            )
        if not self.session_secret:
            raise InboxConfigError(f"{PREFIX}SESSION_SECRET is not set")
        if len(self.session_secret) < MIN_SECRET_CHARS:
            raise InboxConfigError(
                f"{PREFIX}SESSION_SECRET must be at least {MIN_SECRET_CHARS} characters "
                '(python -c "import secrets; print(secrets.token_urlsafe(48))")'
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> InboxSettings:
        env = os.environ if env is None else env
        defaults = cls(passphrase_hash="", session_secret="")
        settings = cls(
            passphrase_hash=env.get(f"{PREFIX}PASSPHRASE_HASH", ""),
            session_secret=env.get(f"{PREFIX}SESSION_SECRET", ""),
            checkpointer_url=env.get(f"{PREFIX}CHECKPOINTER_URL", defaults.checkpointer_url),
            lock_path=Path(env.get(f"{PREFIX}LOCK_PATH", str(defaults.lock_path))),
            port=int(env.get(f"{PREFIX}PORT", str(defaults.port))),
        )
        settings.check()
        return settings


@contextmanager
def single_process_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `path` while the app runs.

    The inbox keeps its per-thread decision locks in memory, so exactly one process may
    serve it. A second process (another `serve`, or uvicorn `--workers 2`) fails here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:  # closing the file releases the lock
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InboxConfigError(
                f"another inbox process is already running (lock held on {path}); "
                "the inbox runs as a single process"
            ) from exc
        yield
