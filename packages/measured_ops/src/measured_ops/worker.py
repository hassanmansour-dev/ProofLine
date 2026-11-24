"""`python -m measured_ops.worker` (C10, ADR-0023): scheduled jobs, the kill switch,
and moving the local logs into Postgres.

    digest [--hours 12] [--out outputs/digests]      write one digest now
    run [--at 07:00 --at 21:00] [--tz America/Toronto] [--out ...]
                                                     write a digest at each time, every day,
                                                     until stopped (one process: lock file)
    kill --reason TEXT                               engage the kill switch
    release                                          release it
    status                                           kill switch, budget, logs backend
    import-logs                                      copy .data/*.jsonl into MEASURED_OPS_LOGS_URL

Reads `MEASURED_OPS_INBOX_CHECKPOINTER_URL` (the inbox's database), `MEASURED_OPS_POLICY_PATH`,
`MEASURED_OPS_PRICES_PATH`, `MEASURED_OPS_BUDGET_PATH`, `MEASURED_OPS_KILL_SWITCH_PATH`.

It runs no agent: digests only read. Moving revisions and scheduled agent runs here (from
the inbox, ADR-0019) waits for C6, which creates tasks; the budget and the kill switch
already apply to every supervisor run wherever it happens.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from measured_ops.budget import budget_from_env
from measured_ops.checkpoint import open_checkpointer
from measured_ops.digest import FileDigestSink, build_digest
from measured_ops.inbox.service import Inbox
from measured_ops.inbox.settings import (
    InboxConfigError,
    checkpointer_url,
    env_path,
    single_process_lock,
)
from measured_ops.killswitch import KillSwitch
from measured_ops.policy import load_policy
from measured_ops.prices import load_model_prices
from measured_ops.supervisor import build_supervisor

log = logging.getLogger("measured_ops.worker")

DEFAULT_TIMES = ("07:00", "21:00")  # GROW-89: daily digests 7 AM / 9 PM
DEFAULT_TZ = "America/Toronto"
DEFAULT_OUT = Path("outputs/digests")
LOCK = Path(".data/worker.lock")


def parse_times(values: Sequence[str]) -> list[time]:
    out = []
    for value in values:
        try:
            hour, minute = value.split(":")
            out.append(time(int(hour), int(minute)))
        except ValueError:
            raise ValueError(f"times are HH:MM, got {value!r}") from None
    if not out:
        raise ValueError("at least one time")
    return sorted(set(out))


def next_run(now: datetime, times: Sequence[time], tz: ZoneInfo) -> datetime:
    """Pure: the first of `times` (local to `tz`) strictly after `now`, as an aware datetime.
    Local times are resolved per day, so the schedule follows daylight-saving changes."""
    local = now.astimezone(tz)
    for days in range(0, 3):
        day = (local + timedelta(days=days)).date()
        for t in sorted(times):
            candidate = datetime.combine(day, t, tzinfo=tz)
            if candidate > local:
                return candidate
    raise AssertionError("unreachable: a time exists within two days")


async def write_digest(out: Path, hours: float) -> str:
    switch = KillSwitch.from_env()
    async with open_checkpointer(checkpointer_url()) as saver:
        policy = load_policy(env_path("MEASURED_OPS_POLICY_PATH"))
        graph = build_supervisor([], policy).compile(checkpointer=saver)  # reads only
        prices = load_model_prices(env_path("MEASURED_OPS_PRICES_PATH"))
        inbox = Inbox(graph, policy, prices=prices, callbacks=[], kill_switch=switch)
        digest = await build_digest(
            inbox, prices=prices, kill_switch=switch, window=timedelta(hours=hours)
        )
    where = FileDigestSink(out).deliver(digest)
    log.info("worker: digest written (%d waiting, %d stopped)", digest.waiting, digest.stopped)
    return where


async def run_schedule(times: Sequence[time], tz: ZoneInfo, out: Path, hours: float) -> None:
    while True:
        due = next_run(datetime.now(UTC), times, tz)
        log.info("worker: next digest at %s", due.isoformat())
        await asyncio.sleep(max(0.0, (due - datetime.now(UTC)).total_seconds()))
        try:
            await write_digest(out, hours)
        except Exception:
            # one failed digest must not stop the schedule; the next one may work
            log.exception("worker: digest failed; next one at the next scheduled time")


def cmd_status() -> int:
    switch = KillSwitch.from_env()
    reason = switch.reason()
    print(f"kill switch: {'ON ' + reason if reason else 'off'} ({switch.path})")
    budget = budget_from_env()
    print(f"budget per run: {budget if budget else 'none (MEASURED_OPS_BUDGET_PATH unset)'}")
    from measured_ops.pg_logs import logs_url

    print(f"judge log and posted marks: {'Postgres' if logs_url() else 'JSONL files in .data/'}")
    return 0


def cmd_import_logs() -> int:
    from measured_ops.judge_log import DEFAULT_PATH as JUDGE_DEFAULT
    from measured_ops.judge_log import ENV as JUDGE_ENV
    from measured_ops.pg_logs import PostgresJudgeLog, PostgresPostedLog, logs_url
    from measured_ops.posted import DEFAULT_PATH as POSTED_DEFAULT
    from measured_ops.posted import ENV as POSTED_ENV

    url = logs_url()
    if not url:
        print("set MEASURED_OPS_LOGS_URL=postgresql://... first", file=sys.stderr)
        return 2
    judge = env_path(JUDGE_ENV) or JUDGE_DEFAULT
    posted = env_path(POSTED_ENV) or POSTED_DEFAULT
    added_j = PostgresJudgeLog(url).import_jsonl(judge)
    added_p = PostgresPostedLog(url).import_jsonl(posted)
    print(f"judge log: {added_j} new line(s) from {judge}; posted: {added_p} new mark(s)")
    print("The JSONL files are left as they are.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m measured_ops.worker")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("digest", help="write one digest now")
    one.add_argument("--hours", type=float, default=12.0)
    one.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run = sub.add_parser("run", help="write digests on a daily schedule")
    run.add_argument("--at", action="append", dest="times")
    run.add_argument("--tz", default=DEFAULT_TZ)
    run.add_argument("--hours", type=float, default=12.0)
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    kill = sub.add_parser("kill", help="engage the kill switch")
    kill.add_argument("--reason", required=True)
    sub.add_parser("release", help="release the kill switch")
    sub.add_parser("status", help="kill switch, budget and logs backend")
    sub.add_parser("import-logs", help="copy the JSONL logs into Postgres")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.command == "digest":
        print(asyncio.run(write_digest(args.out, args.hours)))
        return 0
    if args.command == "run":
        times = parse_times(args.times or DEFAULT_TIMES)
        try:
            with single_process_lock(LOCK):
                asyncio.run(run_schedule(times, ZoneInfo(args.tz), args.out, args.hours))
        except InboxConfigError:
            print(f"another worker is already running (lock held on {LOCK})", file=sys.stderr)
            return 2
        return 0
    if args.command == "kill":
        KillSwitch.from_env().engage(args.reason)
        print("kill switch ON: no agent runs until `release`")
        return 0
    if args.command == "release":
        print("kill switch released" if KillSwitch.from_env().release() else "it was off")
        return 0
    if args.command == "status":
        return cmd_status()
    return cmd_import_logs()


if __name__ == "__main__":
    raise SystemExit(main())
