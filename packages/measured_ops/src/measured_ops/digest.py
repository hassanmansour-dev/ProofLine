"""Daily digest (C10, ADR-0023): what needs Hassan, what's stuck, what it cost.

Read-only: it reads the checkpointer through the inbox service and writes Markdown. It
calls no model, resumes no run and sends nothing anywhere. Where a digest goes is a
`DigestSink`; the only sink here writes files under `outputs/digests/` (git-ignored).
Delivering it to the phone (email, push, a chat message) is a separate decision: any of
those messages a real person through an outside service, so it waits for Hassan.

Sections: waiting for you (oldest first, with how long), stopped, failed (ended with an
agent error or a budget stop, in the window) and in-progress runs,
approved drafts not yet posted, the cost of runs active in the window (whole runs, priced
with the price table; an unpriced model shows "unknown", never 0), and the kill switch.
Goals and previews are Hassan's own text and stay in his local file; targets never
appear (they can be addresses).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from measured_evals import Usage, total_by_agent
from measured_evals.cost import Prices
from measured_ops.inbox.service import Inbox
from measured_ops.killswitch import KillSwitch

GOAL_MAX = 100


@dataclass(frozen=True)
class Digest:
    created_at: datetime
    since: datetime
    markdown: str
    waiting: int
    stopped: int


class DigestSink(Protocol):
    def deliver(self, digest: Digest) -> str:
        """Deliver it; return where it went (a path, an id)."""
        ...


class FileDigestSink:
    """Writes `<dir>/<YYYY-MM-DD-HHMM>.md` (UTC). Overwrites a digest of the same minute."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def deliver(self, digest: Digest) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{digest.created_at.astimezone(UTC):%Y-%m-%d-%H%M}.md"
        path.write_text(digest.markdown, encoding="utf-8")
        return str(path)


def _short(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= GOAL_MAX else one[: GOAL_MAX - 1] + "…"


def _ago(then: str, now: datetime) -> str:
    try:
        when = datetime.fromisoformat(then)
    except ValueError:
        return "unknown"
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:,.4f}"


async def _window_usage(inbox: Inbox, since: datetime) -> list[Usage]:
    usages: list[Usage] = []
    graph: Any = inbox.graph
    for thread_id in await inbox.thread_ids():
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        try:
            touched = datetime.fromisoformat(str(snap.created_at))
        except ValueError:
            continue
        if touched >= since:
            usages.extend((snap.values or {}).get("usage") or [])
    return usages


async def build_digest(
    inbox: Inbox,
    *,
    prices: Prices,
    kill_switch: KillSwitch,
    now: datetime | None = None,
    window: timedelta = timedelta(hours=12),
) -> Digest:
    now = now or datetime.now(UTC)
    since = now - window
    waiting = await inbox.list_pending()
    running = await inbox.in_progress()
    ready = await inbox.list_ready()
    stopped = [r for r in running if r.state == "stopped"]
    others = [r for r in running if r.state != "stopped"]
    lines = [f"# Measured Ops digest — {now.astimezone(UTC):%Y-%m-%d %H:%M} UTC", ""]

    reason = kill_switch.reason()
    if reason is not None:
        lines += [f"**Kill switch is ON**: no agent runs. {reason}", ""]

    lines.append(f"## Waiting for you ({len(waiting)})")
    lines += [
        f"- {_short(i.goal)} · {i.type} · {i.risk_level} risk · waiting {_ago(i.paused_at, now)}"
        + (" · check failed" if i.eval is not None and not i.eval.passed else "")
        for i in waiting
    ] or ["- Nothing."]
    lines += ["", f"## Stopped ({len(stopped)}): Retry in the inbox"]
    lines += [f"- {_short(r.goal)} · {r.agent}" for r in stopped] or ["- Nothing."]
    failed = await inbox.failed_runs(now=now, window=window)
    lines += ["", f"## Failed ({len(failed)}): ended, nothing more is coming"] if failed else []
    lines += [f"- {_short(f.goal)} · {f.agent} · {_short(f.error)}" for f in failed]
    if others:
        lines += ["", f"## In progress ({len(others)})"]
        lines += [f"- {_short(r.goal)} · {r.agent} · {r.state}" for r in others]
    lines += ["", f"## Approved drafts not posted yet ({len(ready)})"]
    lines += [f"- {_short(r.goal)} · approved {_ago(r.approved_at, now)} ago" for r in ready] or [
        "- Nothing."
    ]

    totals = total_by_agent(await _window_usage(inbox, since), prices)
    lines += ["", f"## Cost of runs active since {since.astimezone(UTC):%Y-%m-%d %H:%M} UTC"]
    lines += [
        f"- {agent}: {t.tokens:,} tokens · {_money(t.cost)}" for agent, t in totals.items()
    ] or ["- No model calls."]
    lines.append("")
    return Digest(
        created_at=now,
        since=since,
        markdown="\n".join(lines),
        waiting=len(waiting),
        stopped=len(stopped),
    )
