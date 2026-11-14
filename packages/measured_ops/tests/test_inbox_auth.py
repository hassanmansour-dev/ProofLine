"""Inbox sign-in pieces (C4, ADR-0017): passphrase hash, login throttle, settings that
refuse to start without their secrets, and the single-process lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from measured_ops.inbox.auth import LoginThrottle, hash_passphrase, verify_passphrase
from measured_ops.inbox.settings import InboxConfigError, InboxSettings, single_process_lock

FAST_N = 2**10  # tests only; the default cost is much higher

SECRET = "s" * 40


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "MEASURED_OPS_INBOX_PASSPHRASE_HASH": hash_passphrase("open sesame", n=FAST_N),
        "MEASURED_OPS_INBOX_SESSION_SECRET": SECRET,
    }
    env.update(overrides)
    return env


# ---- passphrase hash ------------------------------------------------------------------


def test_hash_verifies_only_the_right_passphrase() -> None:
    encoded = hash_passphrase("open sesame", n=FAST_N)

    assert encoded.startswith("scrypt$")
    assert "open sesame" not in encoded
    assert verify_passphrase("open sesame", encoded)
    assert not verify_passphrase("open sesame!", encoded)
    assert not verify_passphrase("", encoded)


def test_same_passphrase_hashes_differently_each_time() -> None:
    assert hash_passphrase("x", n=FAST_N) != hash_passphrase("x", n=FAST_N)


def test_malformed_hash_never_verifies() -> None:
    assert not verify_passphrase("anything", "not-a-hash")
    assert not verify_passphrase("anything", "scrypt$1$2$3$!!$!!")


# ---- throttle ---------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


def test_five_failures_lock_sign_in_for_fifteen_minutes() -> None:
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(4):
        throttle.record_failure()
    assert not throttle.locked()

    throttle.record_failure()  # 5th

    assert throttle.locked()
    assert 0 < throttle.retry_after() <= 15 * 60
    clock.t += 15 * 60 + 1
    assert not throttle.locked()


def test_failures_outside_the_window_dont_count() -> None:
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(4):
        throttle.record_failure()
    clock.t += 15 * 60 + 1

    throttle.record_failure()

    assert not throttle.locked()


def test_success_resets_the_count() -> None:
    throttle = LoginThrottle(clock=Clock())
    for _ in range(4):
        throttle.record_failure()

    throttle.reset()
    throttle.record_failure()

    assert not throttle.locked()


# ---- settings: no secrets, no start -----------------------------------------------------


def test_settings_from_env() -> None:
    settings = InboxSettings.from_env(_env())

    assert settings.session_secret == SECRET
    assert settings.session_hours == 12
    assert settings.checkpointer_url.startswith("sqlite:///")


@pytest.mark.parametrize(
    "var", ["MEASURED_OPS_INBOX_PASSPHRASE_HASH", "MEASURED_OPS_INBOX_SESSION_SECRET"]
)
def test_refuses_to_start_without_each_secret(var: str) -> None:
    env = _env()
    del env[var]

    with pytest.raises(InboxConfigError, match=var):
        InboxSettings.from_env(env)


def test_refuses_a_short_session_secret() -> None:
    with pytest.raises(InboxConfigError, match="at least 32"):
        InboxSettings.from_env(_env(MEASURED_OPS_INBOX_SESSION_SECRET="short"))


def test_refuses_a_hash_whose_parameters_cant_run() -> None:
    too_big = "scrypt$1099511627776$8$1$c2FsdA$a2V5"  # n = 2^40: over the memory cap
    with pytest.raises(InboxConfigError, match="can't be checked"):
        InboxSettings.from_env(_env(MEASURED_OPS_INBOX_PASSPHRASE_HASH=too_big))


def test_default_cost_meets_owasp() -> None:
    from measured_ops.inbox.auth import SCRYPT_N

    assert SCRYPT_N >= 2**17


def test_refuses_a_hash_that_isnt_one() -> None:
    with pytest.raises(InboxConfigError, match="hash"):
        InboxSettings.from_env(_env(MEASURED_OPS_INBOX_PASSPHRASE_HASH="hunter2"))


# ---- one process only -------------------------------------------------------------------


def test_a_second_process_lock_on_the_same_file_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "inbox.lock"
    with (
        single_process_lock(lock),
        pytest.raises(InboxConfigError, match="already running"),
        single_process_lock(lock),
    ):
        pass
    with single_process_lock(lock):  # released on exit
        pass
