"""List runs paused at the supervisor's gate, and decide them (C4, ADR-0017). No web code.

- A run is "waiting" when its latest checkpoint is paused at `gate` with an interrupt.
  Threads are found from the checkpointer itself (`alist(None)`): no index of our own.
  That scans every checkpoint, which is fine at v0 volume (revisit in C10).
- An action the policy has tightened to "never" since the pause stays listed, marked
  `blocked_by_policy`: hiding it would leave the run paused forever. Only rejecting
  ends it; the gate refuses an approval (`check_decision`).
- `decide` resumes with `Command(resume={interrupt_id: decision})`, never
  `ainvoke(None)`: after a refused answer the refused value is still the thread's
  pending resume, and a bare re-run would replay it (ADR-0016).
- Each item carries the eval node's checks (from the gate payload: what the gate
  showed) and the run's tokens and cost per agent (C7, ADR-0018). A run paused
  before C7 has no checks; it still lists, as "not evaluated".
- Revisions run in the background (C8, ADR-0019). `decide` commits the gate step first
  (`interrupt_after=["gate"]`), so the decision is saved before the phone gets its
  answer; if the next step is `revise`, the rest runs as a background task while the
  agent revises for minutes. At most one revision runs at a time (a semaphore); each has
  a timeout. While queued or running, the thread is not "waiting" (a second tap is
  `AlreadyDecided`) and shows in `in_progress()`.
- "Stopped" (cut off: timeout, error, restart): a run that stopped here, or after a gate
  step (next `revise`, or a gate with no question pending), or between nodes on a
  revision task: only the inbox makes those. A first pass between nodes may be
  `proofline propose` in another process, which runs under the same timeout: it's
  "elsewhere" (left alone) until twice the timeout has passed. `retry()` continues a
  stopped run and `start()` continues each one once, with `ainvoke(None)`: safe there
  because no interrupt is pending (ADR-0016 forbids it only at a pending gate). C10
  moves this to a worker with budget caps.
- Approved actions run in `decide` (C5, ADR-0022): after the gate step it continues through
  `execute` synchronously (a tool call takes seconds) and stops before a `revise`, which goes
  to the background as above. If an execution fails, the decision still stands, the run is
  "stopped" (Retry continues it; executors read before they write, so nothing applies
  twice) and the outcome says so. A run waiting to execute an action a human approved is
  "stopped" at once; one executing an "auto" action may be another process's first pass.
- Double taps: the interrupt id is the real guard (an answer for an interrupt that is
  no longer pending is `AlreadyDecided`). The per-thread lock only serialises two taps
  arriving at once, and it is in-process, so the inbox runs as one process (ADR-0017).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.types import Command
from pydantic import ValidationError

from measured_evals import ModelPrice, total_by_agent
from measured_evals.scores import DEFAULT_THRESHOLD
from measured_ops.contracts import ActionOutcome, ApprovalDecision, OpsState, PendingAction, Task
from measured_ops.judge_log import JudgeLog, judge_log_from_env
from measured_ops.killswitch import KillSwitch, KillSwitchEngaged
from measured_ops.policy import ActionBlockedError, ApprovalPolicy
from measured_ops.posted import PostedLog, posted_log_from_env
from measured_ops.prices import load_model_prices
from measured_ops.supervisor import DEFAULT_MAX_REVISIONS, task_revision, unexecuted
from measured_ops.tracing import masked_tracing_callbacks

log = logging.getLogger("measured_ops.inbox")

RECURSION_LIMIT = 40
DEFAULT_REVISION_TIMEOUT_S = 600.0
DEFAULT_EXECUTE_TIMEOUT_S = 120.0  # running approved actions after one decision
RunState = Literal["queued", "running", "stopped", "elsewhere"]


class NotReady(LookupError):
    """No approved, paste-ready draft has this action id (ADR-0020)."""


class AlreadyDecided(LookupError):
    """The run isn't waiting on this interrupt any more (decided, or never paused)."""


class Refused(ValueError):
    """The gate (or the decision model) refused this answer. The run is still paused."""


@dataclass(frozen=True)
class CheckView:
    name: str
    passed: bool
    score: float
    reason: str | None  # a few words, never the matched text


@dataclass(frozen=True)
class EvalView:
    evaluator: str
    passed: bool
    checks: tuple[CheckView, ...]


@dataclass(frozen=True)
class CostView:
    agent: str
    tokens: int
    cost: float | None  # None: a model in this run has no price ("unknown")


def _eval_view(block: Any) -> EvalView | None:
    """The gate payload's eval block as the inbox shows it; None for a pre-C7 pause."""
    if not isinstance(block, dict):
        return None
    reasons: dict[str, str] = block.get("reasons") or {}
    scores: dict[str, float] = block.get("scores") or {}
    if "failing" in block:
        failing = set(block.get("failing") or ())
    else:  # saved before `failing` existed (early C7): below the default threshold
        failing = {n for n, v in scores.items() if float(v) < DEFAULT_THRESHOLD}
    checks = tuple(
        CheckView(name, name not in failing, float(score), reasons.get(name))
        for name, score in (block.get("scores") or {}).items()
    )
    return EvalView(str(block.get("evaluator", "")), bool(block.get("passed")), checks)


@dataclass(frozen=True)
class PendingItem:
    """One action waiting at the gate, as the inbox shows it."""

    thread_id: str
    interrupt_id: str
    paused_at: str  # ISO timestamp of the paused checkpoint
    task_id: str
    agent: str
    goal: str
    action_id: str
    type: str
    target: str
    preview: str
    risk_level: str
    response_schema: dict[str, Any]
    blocked_by_policy: bool
    eval: EvalView | None = None  # None: paused before the eval node existed (pre-C7)
    revision: int = 0  # the task's revision number (ADR-0019): 0 = first draft
    max_revisions: int = DEFAULT_MAX_REVISIONS
    # None: the run predates usage tracking (pre-C7), which is not the same as free
    costs: tuple[CostView, ...] | None = ()


DEFAULT_PASTE_TYPES = frozenset({"approve_draft"})


@dataclass(frozen=True)
class ReadyItem:
    """An approved draft to paste (ADR-0020), its preview split into blocks."""

    thread_id: str
    action_id: str
    goal: str
    type: str
    approved_at: str  # ISO timestamp of the approval
    preview: str
    blocks: tuple[tuple[str, str], ...]  # (heading, text), in preview order
    posted: bool = False


def split_blocks(preview: str) -> tuple[tuple[str, str], ...]:
    """The preview's `## ` sections as (heading, text); one "Draft" block if none."""
    blocks: list[tuple[str, list[str]]] = []
    for line in preview.splitlines():
        if line.startswith("## "):
            blocks.append((line[3:].strip(), []))
        elif blocks:
            blocks[-1][1].append(line)
    if not blocks:
        return (("Draft", preview.strip()),)
    return tuple((title, "\n".join(body).strip()) for title, body in blocks)


async def list_ready(
    graph: Any,
    thread_ids: list[str],
    *,
    posted: set[str],
    paste_types: frozenset[str] = DEFAULT_PASTE_TYPES,
    include_posted: bool = False,
) -> list[ReadyItem]:
    """Approved drafts of `paste_types` across `thread_ids`, newest approval first."""
    items: list[ReadyItem] = []
    for thread_id in thread_ids:
        try:
            snap = await graph.aget_state(_cfg(thread_id))
        except Exception:
            log.exception("inbox: can't read run %s; skipped", thread_id)
            continue
        values = snap.values or {}
        task: Task | None = values.get("task")
        approved_at = {
            d.action_id: d.decided_at.isoformat()
            for d in values.get("decisions") or []
            if d.approved
        }
        for action in values.get("ready") or []:
            if action.type not in paste_types or action.id not in approved_at:
                continue
            is_posted = action.id in posted
            if is_posted and not include_posted:
                continue
            items.append(
                ReadyItem(
                    thread_id=thread_id,
                    action_id=action.id,
                    goal=task.goal if task is not None else "",
                    type=action.type,
                    approved_at=approved_at[action.id],
                    preview=action.preview,
                    blocks=split_blocks(action.preview),
                    posted=is_posted,
                )
            )
    return sorted(items, key=lambda i: i.approved_at, reverse=True)


@dataclass(frozen=True)
class FailedRun:
    """A run that ended with a failed agent result (e.g. stopped by its budget): nothing
    is waiting and nothing will come back, so the human has to be told."""

    thread_id: str
    agent: str
    goal: str
    task_id: str
    error: str
    ended_at: str


FAILED_WINDOW = timedelta(days=7)  # how long an ended, failed run stays on the list
ERROR_MAX = 300


@dataclass(frozen=True)
class RunStatus:
    """A run that isn't waiting for a human: revising here, cut off, or busy elsewhere."""

    thread_id: str
    agent: str
    goal: str
    state: RunState


@dataclass(frozen=True)
class Outcome:
    thread_id: str
    approved: bool
    still_paused: bool  # the run paused again, on its next action
    revising: bool = False  # a rejection with feedback: the revision runs in the background
    executed: tuple[ActionOutcome, ...] = ()  # what ran during this decision (C5)
    failed: bool = False  # running an approved action failed: the run is stopped (C5)
    kill_switch: bool = False  # ... because the kill switch is on (C10)
    timed_out: bool = False  # ... because it took longer than the execute timeout


def _cfg(thread_id: str) -> Any:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def _reason(exc: ValidationError) -> str:
    msgs = (str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
    return "; ".join(msgs) or str(exc)


class Inbox:
    """Wraps a compiled supervisor graph that has a checkpointer.

    `graph` is typed `Any` so tests can pass a thin wrapper; it must offer
    `checkpointer`, `aget_state` and `ainvoke` like a `CompiledStateGraph`.
    """

    def __init__(
        self,
        graph: Any,
        policy: ApprovalPolicy,
        *,
        prices: Mapping[str, ModelPrice] | None = None,
        callbacks: list[BaseCallbackHandler] | None = None,
        revision_timeout_s: float = DEFAULT_REVISION_TIMEOUT_S,
        execute_timeout_s: float = DEFAULT_EXECUTE_TIMEOUT_S,
        judge_log: JudgeLog | None = None,
        max_revisions: int = DEFAULT_MAX_REVISIONS,
        posted: PostedLog | None = None,
        paste_types: frozenset[str] = DEFAULT_PASTE_TYPES,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        if getattr(graph, "checkpointer", None) is None:
            raise ValueError("the inbox needs a supervisor graph compiled with a checkpointer")
        self.graph = graph
        self.policy = policy
        self.prices = load_model_prices() if prices is None else prices
        # PII-masked LangSmith tracer when tracing is on (ADR-0018); [] otherwise
        self.callbacks = masked_tracing_callbacks() if callbacks is None else callbacks
        self.revision_timeout_s = revision_timeout_s
        self.execute_timeout_s = execute_timeout_s  # running approved actions, per decision
        self.max_revisions = max_revisions  # the supervisor's cap, shown on each item
        self.posted = posted_log_from_env() if posted is None else posted
        self.paste_types = paste_types
        self.kill_switch = kill_switch or KillSwitch.from_env()  # C10, ADR-0023
        self.judge_log: JudgeLog = judge_log_from_env() if judge_log is None else judge_log
        self._revision_slot = asyncio.Semaphore(1)  # one revision at a time (ADR-0019)
        self._revisions: dict[str, Literal["queued", "running"]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped_here: set[str] = set()  # cut off in this process: stopped, any age
        self._started = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}  # so a lock is dropped once nobody holds it

    async def _thread_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        async for checkpoint in self.graph.checkpointer.alist(None):
            configurable = checkpoint.config["configurable"]
            if configurable.get("checkpoint_ns", ""):
                continue  # a subgraph's checkpoint (older runs): the thread's own is enough
            seen.setdefault(configurable["thread_id"], None)
        return list(seen)

    async def thread_ids(self) -> list[str]:
        """Every supervisor thread in the checkpointer (the digest reads them, C10)."""
        return await self._thread_ids()

    async def get(self, thread_id: str) -> PendingItem | None:
        """The action `thread_id` is waiting on, or None if it isn't waiting."""
        if thread_id in self._revisions:
            return None  # revising: not waiting for a human
        snap = await self.graph.aget_state(_cfg(thread_id))
        if snap.next != ("gate",) or not snap.interrupts:
            return None
        interrupt = snap.interrupts[0]
        item = self._item(thread_id, snap, interrupt.value)
        return replace(
            item,
            interrupt_id=interrupt.id,
            response_schema=dict(getattr(interrupt, "response_schema", None) or {}),
        )

    def _item(self, thread_id: str, snap: Any, payload: dict[str, Any]) -> PendingItem:
        task: Task = snap.values["task"]
        action: PendingAction = snap.values["pending_actions"][0]
        usage = snap.values.get("usage")
        costs = (
            None
            if usage is None
            else tuple(
                CostView(a, t.tokens, t.cost) for a, t in total_by_agent(usage, self.prices).items()
            )
        )
        return PendingItem(
            thread_id=thread_id,
            interrupt_id="",
            paused_at=str(snap.created_at or ""),
            task_id=task.id,
            agent=task.agent,
            goal=task.goal,
            action_id=payload["action_id"],
            type=payload["type"],
            target=payload["target"],
            preview=payload["preview"],
            risk_level=payload["risk_level"],
            response_schema={},
            blocked_by_policy=self.policy.is_blocked(action),  # logs a WARNING if so
            eval=_eval_view(payload.get("eval")),
            revision=task_revision(task) or 0,
            max_revisions=self.max_revisions,
            costs=costs,
        )

    async def list_pending(self) -> list[PendingItem]:
        """Every waiting action, oldest pause first. A thread that can't be read (a state
        shape from another version, a foreign graph in a shared database) is logged and
        skipped: one bad run must not hide the others."""
        items: list[PendingItem] = []
        for thread_id in await self._thread_ids():
            try:
                item = await self.get(thread_id)
            except Exception:
                log.exception("inbox: can't read run %s; skipped", thread_id)
                continue
            if item is not None:
                items.append(item)
        return sorted(items, key=lambda i: i.paused_at)

    async def decide(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        action_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> Outcome:
        """Answer the interrupt `thread_id` is waiting on and resume the run.

        Raises AlreadyDecided if that interrupt isn't pending any more, and Refused if
        the answer is invalid (wrong action, feedback too long, approving a "never"
        action). After Refused the run is still paused; answer again with a new call.
        """
        async with self._thread_lock(thread_id) as lock:
            return await self._decide_locked(
                lock, thread_id, interrupt_id, action_id, approved, feedback
            )

    @asynccontextmanager
    async def _thread_lock(self, thread_id: str) -> AsyncIterator[asyncio.Lock]:
        """The thread's lock (not yet held), dropped once nobody holds or waits for it."""
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        self._lock_users[thread_id] = self._lock_users.get(thread_id, 0) + 1
        try:
            yield lock
        finally:
            self._lock_users[thread_id] -= 1
            if not self._lock_users[thread_id]:
                del self._lock_users[thread_id], self._locks[thread_id]

    async def _decide_locked(
        self,
        lock: asyncio.Lock,
        thread_id: str,
        interrupt_id: str,
        action_id: str,
        approved: bool,
        feedback: str | None,
    ) -> Outcome:
        async with lock:
            item = await self.get(thread_id)
            if item is None or item.interrupt_id != interrupt_id:
                raise AlreadyDecided(f"run {thread_id} is not waiting on interrupt {interrupt_id}")
            text = (feedback or "").strip() or None
            try:
                decision = ApprovalDecision(action_id=action_id, approved=approved, feedback=text)
            except ValidationError as exc:
                raise Refused(_reason(exc)) from exc
            resume: Command[Any] = Command(resume={interrupt_id: decision.model_dump(mode="json")})
            cfg = {**_cfg(thread_id), "callbacks": self.callbacks}
            try:
                # Commit the gate step, and only it, before answering: the decision is
                # saved even if what comes next (a revision) waits, fails or is cut off.
                await self.graph.ainvoke(resume, cfg, interrupt_after=["gate"])
            except ValidationError as exc:  # the gate's answer_schema refused it
                raise Refused(_reason(exc)) from exc
            except ActionBlockedError as exc:
                raise Refused(str(exc)) from exc
            # saved: now it's a decision. In a thread: the log may be Postgres (C10).
            await asyncio.to_thread(self._log_decision, item, approved=approved)
            log.info(
                "inbox: run %s action %s %s",
                thread_id,
                action_id,
                "approved" if approved else "rejected",
            )
            snap = await self.graph.aget_state(_cfg(thread_id))
            executed: tuple[ActionOutcome, ...] = ()
            if snap.next in (("execute",), ("gate",)):
                # Run what was just approved (C5), then pause on the round's next action;
                # stop before a revision, which goes to the background below.
                before = len(snap.values.get("executed") or [])
                try:
                    await asyncio.wait_for(
                        self.graph.ainvoke(None, cfg, interrupt_before=["revise"]),
                        timeout=self.execute_timeout_s,
                    )
                except TimeoutError:
                    # Cut off, possibly mid-write: Retry is safe, executors read first.
                    log.error(
                        "inbox: run %s: running an approved action took over %ss; stopped "
                        "(the decision stands; retry from the inbox)",
                        thread_id,
                        self.execute_timeout_s,
                    )
                    self._stopped_here.add(thread_id)
                    return Outcome(
                        thread_id=thread_id,
                        approved=approved,
                        still_paused=False,
                        failed=True,
                        timed_out=True,
                    )
                except KillSwitchEngaged:
                    log.warning(
                        "inbox: run %s not continued: the kill switch is on; the decision "
                        "stands and the action runs after release",
                        thread_id,
                    )
                    self._stopped_here.add(thread_id)
                    return Outcome(
                        thread_id=thread_id,
                        approved=approved,
                        still_paused=False,
                        failed=True,
                        kill_switch=True,
                    )
                except Exception:
                    log.exception(
                        "inbox: run %s stopped while running an approved action; "
                        "the decision stands (retry from the inbox)",
                        thread_id,
                    )
                    self._stopped_here.add(thread_id)
                    return Outcome(
                        thread_id=thread_id, approved=approved, still_paused=False, failed=True
                    )
                snap = await self.graph.aget_state(_cfg(thread_id))
                executed = tuple((snap.values.get("executed") or [])[before:])
            if snap.next == ("revise",):
                # A revision runs the agent for minutes: in the background (ADR-0019).
                self._schedule(thread_id, None)
                return Outcome(
                    thread_id=thread_id,
                    approved=approved,
                    still_paused=False,
                    revising=True,
                    executed=executed,
                )
            still_paused = snap.next == ("gate",) and bool(snap.interrupts)
            return Outcome(
                thread_id=thread_id, approved=approved, still_paused=still_paused, executed=executed
            )

    def _log_decision(self, item: PendingItem, *, approved: bool) -> None:
        """Judge-agreement log: ids, eval scores and the decision; never text (ADR-0019)."""
        scores = None if item.eval is None else {c.name: c.score for c in item.eval.checks}
        try:
            self.judge_log.record_decision(item.action_id, scores, approved)
        except Exception:  # a file error or a database error
            # the decision stands; a lost log line must not undo or hide it
            log.exception("inbox: couldn't write the judge log for action %s", item.action_id)

    # ---- ready to paste (C8, ADR-0020) ------------------------------------------------

    async def list_ready(self, *, include_posted: bool = False) -> list[ReadyItem]:
        """Approved drafts to paste, newest first; posted ones only if asked."""
        return await list_ready(
            self.graph,
            await self._thread_ids(),
            posted=await asyncio.to_thread(self.posted.posted),
            paste_types=self.paste_types,
            include_posted=include_posted,
        )

    async def mark_posted(self, action_id: str) -> None:
        """Record that Hassan posted it, and hide it. Never resumes or writes to a run:
        there is no executor for a draft (ADR-0019); this is only a note (ADR-0020)."""
        ready = await self.list_ready(include_posted=True)
        if action_id not in {r.action_id for r in ready}:
            raise NotReady(f"no approved draft {action_id}")
        await asyncio.to_thread(self.posted.mark, action_id)
        log.info("inbox: draft %s marked as posted", action_id)

    # ---- background revisions (C8, ADR-0019) ----------------------------------------

    def _schedule(self, thread_id: str, value: Command[Any] | None) -> None:
        self._stopped_here.discard(thread_id)
        self._revisions[thread_id] = "queued"
        task = asyncio.create_task(self._revise(thread_id, value), name=f"revise:{thread_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _revise(self, thread_id: str, value: Command[Any] | None) -> None:
        try:
            async with self._revision_slot:
                self._revisions[thread_id] = "running"
                await asyncio.wait_for(
                    self.graph.ainvoke(value, {**_cfg(thread_id), "callbacks": self.callbacks}),
                    timeout=self.revision_timeout_s,
                )
                failed = await self._failed(thread_id)
                if failed is None:
                    log.info("inbox: run %s revised", thread_id)
                else:
                    # No new draft is coming: say so loudly; the page and the digest list it.
                    # Only a budget stop's text is logged (cap names and numbers); any other
                    # agent error stays out of the log.
                    budget = failed.error.startswith("budget:")
                    log.error(
                        "inbox: run %s ended as failed: %s",
                        thread_id,
                        failed.error if budget else "agent error (see the inbox)",
                    )
                return
        except TimeoutError:
            log.error(
                "inbox: revision of run %s timed out after %ss; stopped (retry from the inbox)",
                thread_id,
                self.revision_timeout_s,
            )
        except asyncio.CancelledError:
            log.warning("inbox: revision of run %s cancelled; stopped", thread_id)
            raise
        except Exception:
            log.exception("inbox: revision of run %s failed; stopped", thread_id)
        finally:
            self._revisions.pop(thread_id, None)
        self._stopped_here.add(thread_id)  # reached only if it didn't finish

    async def _status(self, thread_id: str) -> RunStatus | None:
        snap = await self.graph.aget_state(_cfg(thread_id))
        values = snap.values or {}
        task: Task | None = values.get("task")
        if task is None:
            return None
        if thread_id in self._revisions:
            return RunStatus(thread_id, task.agent, task.goal, self._revisions[thread_id])
        if not snap.next or (snap.next == ("gate",) and snap.interrupts):
            return None  # finished, or waiting for a human
        if (
            thread_id in self._stopped_here
            or snap.next
            in (("gate",), ("revise",))  # after a gate step: only the inbox stops there
            or task.parent_id is not None  # a revision: only the inbox runs those
            or _approved_but_not_run(snap)  # an approved action: only the inbox runs those
        ):
            return RunStatus(thread_id, task.agent, task.goal, "stopped")
        # A first pass between nodes may be a `proofline propose` run in another process.
        # It runs under the same timeout, so twice that is a safe margin before retrying.
        age = _age_s(snap.created_at)
        stale = age is None or age > 2 * self.revision_timeout_s
        state: RunState = "stopped" if stale else "elsewhere"
        return RunStatus(thread_id, task.agent, task.goal, state)

    async def _failed(self, thread_id: str) -> FailedRun | None:
        """The run's failure, if it has ended and its last agent result failed."""
        snap = await self.graph.aget_state(_cfg(thread_id))
        values = snap.values or {}
        results = values.get("results") or []
        task: Task | None = values.get("task")
        if snap.next or task is None or not results or results[-1].status != "failed":
            return None
        return FailedRun(
            thread_id=thread_id,
            agent=task.agent,
            goal=task.goal,
            task_id=task.id,
            error=(results[-1].error or "failed")[:ERROR_MAX],
            ended_at=str(snap.created_at or ""),
        )

    async def failed_runs(
        self, *, now: datetime | None = None, window: timedelta = FAILED_WINDOW
    ) -> list[FailedRun]:
        """Runs that ended as failed within `window`, newest first. Read from the
        checkpointer, so a first pass that failed in another process (`proofline propose`)
        and a restart are covered too."""
        since = (now or datetime.now(UTC)) - window
        out: list[FailedRun] = []
        for thread_id in await self._thread_ids():
            try:
                failed = await self._failed(thread_id)
            except Exception:
                log.exception("inbox: can't read run %s; skipped", thread_id)
                continue
            if failed is None:
                continue
            try:
                recent = datetime.fromisoformat(failed.ended_at) >= since
            except ValueError:
                recent = True  # no usable time: show it
            if recent:
                out.append(failed)
        return sorted(out, key=lambda f: f.ended_at, reverse=True)

    async def in_progress(self) -> list[RunStatus]:
        """Runs that aren't waiting for a human and aren't finished."""
        out: list[RunStatus] = []
        for thread_id in await self._thread_ids():
            try:
                status = await self._status(thread_id)
            except Exception:
                log.exception("inbox: can't read run %s; skipped", thread_id)
                continue
            if status is not None:
                out.append(status)
        return out

    async def retry(self, thread_id: str) -> None:
        """Continue a stopped run in the background. AlreadyDecided if it isn't stopped;
        KillSwitchEngaged while the kill switch is on (C10)."""
        self.kill_switch.check()
        async with self._thread_lock(thread_id) as lock, lock:
            status = await self._status(thread_id)
            if status is None or status.state != "stopped":
                raise AlreadyDecided(f"run {thread_id} is not stopped")
            self._schedule(thread_id, None)
        log.info("inbox: retrying run %s", thread_id)

    async def start(self) -> None:
        """Once per process: continue every run a restart (or a timeout) cut off."""
        if self._started:
            return
        self._started = True
        if self.kill_switch.engaged():
            log.warning("inbox: kill switch is on; not continuing stopped runs (Retry later)")
            return
        for status in await self.in_progress():
            if status.state == "stopped":
                log.info("inbox: continuing stopped run %s", status.thread_id)
                self._schedule(status.thread_id, None)

    async def wait_idle(self) -> None:
        """Wait until no revision is queued or running (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        """Cancel running revisions: their runs stay stopped and continue on next start."""
        for task in list(self._tasks):
            task.cancel()
        await self.wait_idle()


def _approved_but_not_run(snap: Any) -> bool:
    """Waiting to execute an action a human approved (C5): only the inbox gets there."""
    if snap.next != ("execute",):
        return False
    waiting = unexecuted(cast(OpsState, snap.values or {}))
    approved = {d.action_id for d in (snap.values or {}).get("decisions") or [] if d.approved}
    return bool(waiting) and waiting[0].id in approved


def _age_s(created_at: str | None) -> float | None:
    if not created_at:
        return None
    try:
        then = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    return (datetime.now(UTC) - then).total_seconds()
