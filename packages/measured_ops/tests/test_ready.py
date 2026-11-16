"""Ready to paste (C8, ADR-0020): approved drafts, newest first, split into blocks;
"Mark as posted" only records and hides, and never touches a run. Demo data only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from ops_fakes import ScriptedAgent, make_task

from measured_ops import ApprovalDecision, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.inbox.service import Inbox, NotReady, split_blocks
from measured_ops.posted import JsonlPostedLog, posted_log_from_env
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio

PREVIEW = (
    "## LinkedIn post\n\n# Exits make loops safe\n\nEvery loop needs an exit.\n\n"
    "## Clip script (~20s)\n\nEvery loop needs an exit.\n\n"
    "## Lesson outline\n\nAngle.\n\n1. **Cap**: stop after three tries."
)


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _graph(agent: Any, saver: InMemorySaver | None = None) -> Any:
    saver = saver or InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST)
    )
    return build_supervisor([agent], load_policy()).compile(checkpointer=saver)


def _cfg(thread: str) -> Any:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 40}


async def _approved(graph: Any, thread: str, goal: str = "Draft a post.") -> str:
    out = await graph.ainvoke(initial_ops_state(make_task(goal=goal)), _cfg(thread))
    action_id: str = out["__interrupt__"][0].value["action_id"]
    decision = ApprovalDecision(action_id=action_id, approved=True)
    await graph.ainvoke(Command(resume=decision.model_dump()), _cfg(thread))
    return action_id


def test_split_blocks_follows_the_h2_headings() -> None:
    assert split_blocks(PREVIEW) == (
        ("LinkedIn post", "# Exits make loops safe\n\nEvery loop needs an exit."),
        ("Clip script (~20s)", "Every loop needs an exit."),
        ("Lesson outline", "Angle.\n\n1. **Cap**: stop after three tries."),
    )
    assert split_blocks("Just text.") == (("Draft", "Just text."),)


async def test_approved_drafts_are_listed_newest_first_with_their_blocks(
    tmp_path: Path,
) -> None:
    graph = _graph(ScriptedAgent([("approve_draft", "medium", PREVIEW)]))
    older = await _approved(graph, "a", goal="Older.")
    newer = await _approved(graph, "b", goal="Newer.")
    inbox = Inbox(graph, load_policy(), callbacks=[], posted=JsonlPostedLog(tmp_path / "p"))

    ready = await inbox.list_ready()

    assert [r.action_id for r in ready] == [newer, older]
    assert [title for title, _ in ready[0].blocks] == [
        "LinkedIn post",
        "Clip script (~20s)",
        "Lesson outline",
    ]
    assert ready[0].goal == "Newer."


async def test_only_approved_paste_types_are_listed(tmp_path: Path) -> None:
    graph = _graph(
        ScriptedAgent([("approve_draft", "medium", PREVIEW), ("draft_content", "low", "auto")])
    )
    out = await graph.ainvoke(initial_ops_state(make_task()), _cfg("t"))
    pending = out["__interrupt__"][0].value["action_id"]
    decision = ApprovalDecision(action_id=pending, approved=False)
    await graph.ainvoke(Command(resume=decision.model_dump()), _cfg("t"))
    inbox = Inbox(graph, load_policy(), callbacks=[], posted=JsonlPostedLog(tmp_path / "p"))

    assert await inbox.list_ready() == []  # rejected draft no; auto draft_content no


async def test_mark_as_posted_records_and_hides_and_never_touches_the_run(
    tmp_path: Path,
) -> None:
    graph = _graph(ScriptedAgent([("approve_draft", "medium", PREVIEW)]))
    action_id = await _approved(graph, "t")
    before = await graph.aget_state(_cfg("t"))
    path = tmp_path / "posted.jsonl"
    inbox = Inbox(graph, load_policy(), callbacks=[], posted=JsonlPostedLog(path))
    calls: list[Any] = []
    original = graph.ainvoke

    async def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return await original(*args, **kwargs)

    graph.ainvoke = spy

    await inbox.mark_posted(action_id)
    await inbox.mark_posted(action_id)  # twice is harmless

    assert await inbox.list_ready() == []
    assert calls == []  # never resumes or runs anything
    after = await graph.aget_state(_cfg("t"))
    assert (
        after.config["configurable"]["checkpoint_id"]
        == before.config["configurable"]["checkpoint_id"]
    )
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert all(set(line) == {"action_id", "posted_at"} for line in lines)
    assert {line["action_id"] for line in lines} == {action_id}


async def test_marking_something_not_ready_is_refused(tmp_path: Path) -> None:
    graph = _graph(ScriptedAgent([("approve_draft", "medium", PREVIEW)]))
    await graph.ainvoke(initial_ops_state(make_task()), _cfg("t"))  # waiting, not approved
    path = tmp_path / "posted.jsonl"
    inbox = Inbox(graph, load_policy(), callbacks=[], posted=JsonlPostedLog(path))

    with pytest.raises(NotReady):
        await inbox.mark_posted("not-an-approved-draft")
    assert not path.exists()


async def test_list_ready_can_include_posted_drafts(tmp_path: Path) -> None:
    graph = _graph(ScriptedAgent([("approve_draft", "medium", PREVIEW)]))
    action_id = await _approved(graph, "t")
    inbox = Inbox(graph, load_policy(), callbacks=[], posted=JsonlPostedLog(tmp_path / "p"))
    await inbox.mark_posted(action_id)

    [item] = await inbox.list_ready(include_posted=True)
    assert item.posted is True


def test_posted_log_path_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MEASURED_OPS_POSTED_PATH", raising=False)
    assert posted_log_from_env().path == Path(".data/posted.jsonl")
    monkeypatch.setenv("MEASURED_OPS_POSTED_PATH", str(tmp_path / "x.jsonl"))
    assert posted_log_from_env().path == tmp_path / "x.jsonl"
