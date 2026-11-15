"""Content agent end to end (C8, ADR-0019), SQLite and Postgres:

    topic → content agent → eval → pause (child process) → restart → inbox: reject with
    feedback → background revision (feedback + previous preview reach the writer) →
    pause → approve → ready.

Fake LLM and stub researcher: no network, no keys.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from measured_ops import load_policy
from measured_ops.checkpoint import open_checkpointer
from measured_ops.inbox.service import Inbox
from measured_ops.judge_log import JsonlJudgeLog
from measured_ops.supervisor import build_supervisor
from proofline.agent import APPROVE_DRAFT, ContentAgent
from tests.fakes import FakeLLM, make_critique, make_deps, make_draft, make_outline

pytestmark = pytest.mark.anyio

PROOFLINE = Path(__file__).resolve().parents[1]


def _pause_in_another_process(url: str, thread_id: str, tmp: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "tests.content_e2e_child", url, thread_id, str(tmp)],
        cwd=PROOFLINE,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return payload


async def test_topic_to_rejection_to_revision_to_approval(
    checkpointer_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    thread_id = f"content-e2e-{uuid4().hex}"
    first = _pause_in_another_process(checkpointer_url, thread_id, tmp_path)

    assert first["type"] == APPROVE_DRAFT
    assert first["eval"] is not None  # the eval node scored it, in the child
    for heading in ("## LinkedIn post", "## Clip script", "## Lesson outline"):
        assert heading in first["preview"]

    # After the restart: the revision runs here, so this agent's fake answers it.
    llm = FakeLLM().queue(
        make_outline(), make_draft(title="Exits make loops safe to run"), make_critique(9)
    )
    agent = ContentAgent(make_deps(tmp_path, llm))
    judge = JsonlJudgeLog(tmp_path / "judge.jsonl")
    async with open_checkpointer(checkpointer_url) as saver:
        graph = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
        inbox = Inbox(graph, load_policy(), callbacks=[], judge_log=judge)
        [item] = [i for i in await inbox.list_pending() if i.thread_id == thread_id]
        assert item.action_id == first["action_id"]

        outcome = await inbox.decide(
            thread_id,
            interrupt_id=item.interrupt_id,
            action_id=item.action_id,
            approved=False,
            feedback="Make the hook concrete.",
        )
        assert outcome.revising
        await inbox.wait_idle()

        writer_prompt = next(c for c in llm.calls if c.role == "writer").prompt.user
        assert "Make the hook concrete." in writer_prompt
        assert "## LinkedIn post" in writer_prompt  # the rejected preview, to revise from

        [revised] = [i for i in await inbox.list_pending() if i.thread_id == thread_id]
        assert revised.action_id != first["action_id"]
        assert "Exits make loops safe to run" in revised.preview
        await inbox.decide(
            thread_id,
            interrupt_id=revised.interrupt_id,
            action_id=revised.action_id,
            approved=True,
        )
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})

    assert snap.next == ()
    assert [a.id for a in snap.values["ready"]] == [revised.action_id]
    assert snap.values["task"].inputs["revision"] == 1
    assert [d.approved for d in snap.values["decisions"]] == [False, True]
    lines = [json.loads(x) for x in (tmp_path / "judge.jsonl").read_text().splitlines()]
    assert [(x["kind"], x["approved"]) for x in lines if x["kind"] == "decision"] == [
        ("decision", False),
        ("decision", True),
    ]


class HangOnceLLM(FakeLLM):
    """The first writer call of a revision hangs until released (a slow model call)."""

    def __init__(self) -> None:
        import asyncio

        super().__init__()
        self.release = asyncio.Event()
        self.hung = False

    async def generate(self, prompt: Any, schema: Any, *, role: Any, run_config: Any) -> Any:
        if role == "writer" and not self.hung:
            self.hung = True
            await self.release.wait()
        return await super().generate(prompt, schema, role=role, run_config=run_config)


async def test_a_revision_cut_off_inside_the_pipeline_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding: the nested pipeline must not checkpoint into the supervisor's
    # database (its state types aren't in the supervisor's serde allowlist), or a cut-off
    # revision resumes from a checkpoint it can't load, forever.
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    url = f"sqlite:///{tmp_path / 'cp.db'}"
    thread_id = "cut-off"
    _pause_in_another_process(url, thread_id, tmp_path)

    llm = HangOnceLLM()
    # the cut-off pass uses one outline; the retry restarts the pipeline from the planner
    llm.queue(make_outline(), make_outline(), make_draft(), make_critique(9))
    agent = ContentAgent(make_deps(tmp_path, llm))
    async with open_checkpointer(url) as saver:
        graph = build_supervisor([agent], load_policy()).compile(checkpointer=saver)
        inbox = Inbox(graph, load_policy(), callbacks=[], revision_timeout_s=0.3)
        [item] = await inbox.list_pending()
        await inbox.decide(
            thread_id,
            interrupt_id=item.interrupt_id,
            action_id=item.action_id,
            approved=False,
            feedback="Shorter.",
        )
        await inbox.wait_idle()  # timed out inside the writer
        assert [s.state for s in await inbox.in_progress()] == ["stopped"]

        namespaces = {
            c.config["configurable"].get("checkpoint_ns", "")
            async for c in saver.alist({"configurable": {"thread_id": thread_id}})
        }
        assert namespaces == {""}, f"nested checkpoints written: {namespaces}"

        llm.release.set()
        inbox.revision_timeout_s = 30
        await inbox.retry(thread_id)
        await inbox.wait_idle()

        [again] = await inbox.list_pending()
        assert again.action_id != item.action_id
