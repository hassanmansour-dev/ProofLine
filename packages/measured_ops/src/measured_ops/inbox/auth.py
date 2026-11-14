"""Single-user sign-in for the inbox (ADR-0017): a passphrase stored only as a scrypt
hash (standard library), and a throttle that locks sign-in after repeated failures."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import secrets
import time
from collections import deque
from collections.abc import Callable

SCRYPT_N = 2**17  # OWASP's minimum for scrypt: ~128 MiB of memory per check
SCRYPT_R = 8
SCRYPT_P = 1
_MAXMEM = 256 * 1024 * 1024


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_passphrase(passphrase: str, *, n: int = SCRYPT_N) -> str:
    """`scrypt$n$r$p$salt$key`, salt and key url-safe base64. A new salt every call."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(
        passphrase.encode(), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, maxmem=_MAXMEM, dklen=32
    )
    return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def is_passphrase_hash(encoded: str) -> bool:
    parts = encoded.split("$")
    return len(parts) == 6 and parts[0] == "scrypt" and all(p.isdigit() for p in parts[1:4])


def hash_params_usable(encoded: str) -> bool:
    """Whether scrypt can run with this hash's parameters (within the memory cap).
    Checked at startup, so a hash that could never verify fails loudly, not at sign-in."""
    if not is_passphrase_hash(encoded):
        return False
    _, n, r, p, _salt, _key = encoded.split("$")
    try:
        hashlib.scrypt(b"", salt=b"\0" * 16, n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM, dklen=16)
    except (ValueError, OverflowError, MemoryError):
        return False
    return True


def verify_passphrase(passphrase: str, encoded: str) -> bool:
    """Constant-time check. Any malformed hash verifies nothing."""
    if not is_passphrase_hash(encoded):
        return False
    _, n, r, p, salt, key = encoded.split("$")
    try:
        expected = _unb64(key)
        actual = hashlib.scrypt(
            passphrase.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=max(len(expected), 1),
        )
    except ValueError:  # bad base64 or scrypt parameters
        return False
    return bool(expected) and hmac.compare_digest(actual, expected)


class LoginThrottle:
    """`max_failures` failed sign-ins within `window` seconds lock sign-in until the
    oldest of them is `window` seconds old. Global, not per client: there is one user,
    and behind `tailscale serve` every request comes from 127.0.0.1."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window: float = 15 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self.clock = clock
        self._failures: deque[float] = deque()

    def _prune(self) -> None:
        cutoff = self.clock() - self.window
        while self._failures and self._failures[0] <= cutoff:
            self._failures.popleft()

    def locked(self) -> bool:
        self._prune()
        return len(self._failures) >= self.max_failures

    def retry_after(self) -> int:
        """Seconds until sign-in reopens (0 if it's open)."""
        if not self.locked():
            return 0
        return max(1, math.ceil(self._failures[0] + self.window - self.clock()))

    def record_failure(self) -> None:
        self._prune()
        self._failures.append(self.clock())

    def reset(self) -> None:
        self._failures.clear()
