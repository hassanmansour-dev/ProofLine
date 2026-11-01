from __future__ import annotations

import operator
import typing
from pathlib import Path
from typing import Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from proofline import schemas
from proofline.checkpointing import SERDE_ALLOWLIST, open_checkpointer
from proofline.state import ScriptState
from tests.fakes import make_critique, make_draft, make_notes, make_plan


def _models_in(tp: Any) -> set[type[BaseModel]]:
    found: set[type[BaseModel]] = set()
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        found.add(tp)
        for f in tp.model_fields.values():
            found |= _models_in(f.annotation)
    for arg in typing.get_args(tp):
        found |= _models_in(arg)
    return found


def test_every_state_model_is_allowlisted() -> None:
    hints = typing.get_type_hints(ScriptState, include_extras=True)
    reachable: set[type[BaseModel]] = set()
    for tp in hints.values():
        reachable |= _models_in(tp)
    allowed = {(m, n) for m, n in SERDE_ALLOWLIST}
    missing = {(m.__module__, m.__qualname__) for m in reachable} - allowed
    assert not missing, f"add to schemas.STATE_MODELS: {sorted(missing)}"


class _S(TypedDict, total=False):
    plan: schemas.Plan
    research_notes: schemas.ResearchNotes
    draft: schemas.Draft
    critique: schemas.Critique
    history: typing.Annotated[list[schemas.RevisionRecord], operator.add]


async def _write(state: _S) -> dict[str, Any]:
    return {
        "plan": make_plan(),
        "research_notes": make_notes(),
        "draft": make_draft(),
        "critique": make_critique(7),
        "history": [schemas.RevisionRecord(kind="draft", revision=0)],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("scheme", ["memory", "sqlite"])
async def test_state_models_survive_checkpoint_strictly(
    scheme: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    url = "memory://" if scheme == "memory" else f"sqlite:///{tmp_path / 'cp.db'}"
    builder = StateGraph(_S)
    builder.add_node("write", _write)
    builder.add_edge(START, "write")
    builder.add_edge("write", END)
    cfg: Any = {"configurable": {"thread_id": "t1"}}

    async with open_checkpointer(url) as saver:
        await builder.compile(checkpointer=saver).ainvoke({}, cfg)
        if scheme == "memory":
            snap = await builder.compile(checkpointer=saver).aget_state(cfg)
    if scheme == "sqlite":
        async with open_checkpointer(url) as saver:  # reopen: real deserialization from disk
            snap = await builder.compile(checkpointer=saver).aget_state(cfg)

    assert snap.values["draft"] == make_draft()
    assert snap.values["critique"].overall == make_critique(7).overall
    assert isinstance(snap.values["research_notes"].sources[0], schemas.Source)


@pytest.mark.anyio
async def test_unknown_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        async with open_checkpointer("redis://x"):
            pass
