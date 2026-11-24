"""Kill switch (C10, ADR-0023): one flag that stops every agent run before it starts.

The flag is a file: `MEASURED_OPS_KILL_SWITCH_PATH` (an absolute path), default
`~/.measured_ops/kill_switch`. The default is under the home directory, not the working
directory, and a relative path is refused: every process on the machine must watch the
same file wherever it was started. While it exists:
- the supervisor's `call_agent` raises `KillSwitchEngaged` before running any agent, so
  no model is called and nothing is proposed; the run stops between nodes, its state
  saved, and continues normally once the switch is released (Retry, or the next start);
- the supervisor's `execute` raises it before running any executable action, so no
  approved or "auto" write happens either; the decision stays saved and the action runs
  once after release;
- `proofline golden` starts no item;
- the inbox doesn't continue stopped runs at start-up and refuses Retry;
- the worker skips its jobs that would run agents (digests still go out: they only read).

Engage it from anywhere that can touch the file (`python -m measured_ops.worker kill
--reason ...`, or by hand), with no process to reach. Every supervisor gets it by default
(`KillSwitch.from_env()`), so a runner can't forget to wire it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DIR = ".measured_ops"  # under the home directory
DEFAULT_NAME = "kill_switch"
ENV = "MEASURED_OPS_KILL_SWITCH_PATH"
REASON_MAX = 200


class KillSwitchEngaged(RuntimeError):
    """The kill switch is on: no agent runs until it's released."""


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_env(cls) -> KillSwitch:
        raw = os.environ.get(ENV, "").strip()
        if not raw:
            return cls(Path.home() / DEFAULT_DIR / DEFAULT_NAME)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{ENV} must be an absolute path, got {raw!r}")
        return cls(path)

    def engaged(self) -> bool:
        return self.path.exists()

    def reason(self) -> str | None:
        """What the person who engaged it wrote, or None when it's off."""
        try:
            return self.path.read_text(encoding="utf-8").strip() or "(no reason given)"
        except FileNotFoundError:
            return None

    def engage(self, reason: str) -> None:
        text = " ".join(reason.split())[:REASON_MAX] or "(no reason given)"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{datetime.now(UTC).isoformat()} {text}\n", encoding="utf-8")

    def release(self) -> bool:
        """Turn it off. True if it was on."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True

    def check(self) -> None:
        """Raise KillSwitchEngaged if it's on."""
        reason = self.reason()
        if reason is not None:
            raise KillSwitchEngaged(f"kill switch is on ({self.path}): {reason}")
