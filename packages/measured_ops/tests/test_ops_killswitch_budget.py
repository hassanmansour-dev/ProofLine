"""Kill switch and per-run budget caps in the supervisor (C10, ADR-0023)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from ops_fakes import LLMAgent, ScriptedAgent, make_task

from measured_evals import ModelPrice, Usage
from measured_ops import initial_ops_state, load_policy
from measured_ops.budget import Budget, BudgetExceeded, load_budget
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.inbox.service import AlreadyDecided, Inbox
from measured_ops.killswitch import KillSwitch, KillSwitchEngaged
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio
CFG: Any = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
PRICES = {
    "model-a": ModelPrice(
        input=1.0, output=1.0, cache_read=0.1, cache_write_5m=1.25, cache_write_1h=2.0
    )
}


def _reply(model: str = "model-a") -> AIMessage:
    return AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        response_metadata={"model_name": model},
    )


def _saver() -> InMemorySaver:
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))


# ---- kill switch -------------------------------------------------------------------------


def test_the_switch_is_a_file_with_a_reason(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "kill")
    assert not switch.engaged()
    assert switch.reason() is None
    switch.check()

    switch.engage("runaway   loop\non the content agent")

    assert switch.engaged()
    assert switch.reason() is not None
    assert switch.reason().endswith("runaway loop on the content agent")  # type: ignore[union-attr]
    with pytest.raises(KillSwitchEngaged, match="runaway loop"):
        switch.check()
    assert switch.release()
    assert not switch.release()


def test_every_supervisor_reads_the_switch_from_the_environment() -> None:
    # the root conftest points it at tmp_path: a test never sees Hassan's switch
    assert str(KillSwitch.from_env().path) == os.environ["MEASURED_OPS_KILL_SWITCH_PATH"]


async def test_no_agent_runs_while_it_is_on_and_the_run_continues_after() -> None:
    agent = ScriptedAgent([("publish_public", "medium")])
    switch = KillSwitch.from_env()
    switch.engage("testing")
    app = build_supervisor([agent], load_policy()).compile(checkpointer=_saver())

    with pytest.raises(KillSwitchEngaged):
        await app.ainvoke(initial_ops_state(make_task()), CFG)
    assert agent.runs == 0
    assert (await app.aget_state(CFG)).next == ("call_agent",)

    switch.release()
    out = await app.ainvoke(None, CFG)  # continues where it stopped

    assert agent.runs == 1
    assert out["__interrupt__"][0].value["type"] == "publish_public"


async def test_the_inbox_neither_retries_nor_sweeps_while_it_is_on() -> None:
    switch = KillSwitch.from_env()
    agent = ScriptedAgent([("publish_public", "medium")])
    app = build_supervisor([agent], load_policy()).compile(checkpointer=_saver())
    switch.engage("testing")
    with pytest.raises(KillSwitchEngaged):
        await app.ainvoke(initial_ops_state(make_task()), CFG)
    inbox = Inbox(app, load_policy(), callbacks=[])

    with pytest.raises(KillSwitchEngaged):
        await inbox.retry("t")
    await inbox.start()  # logs and skips
    await inbox.wait_idle()
    assert agent.runs == 0

    switch.release()
    with pytest.raises(AlreadyDecided):  # a first pass between nodes: "elsewhere" for now
        await inbox.retry("t")


# ---- budget ------------------------------------------------------------------------------


def test_load_budget(tmp_path: Path) -> None:
    demo = load_budget()
    assert demo.max_calls is not None
    good = tmp_path / "budget.toml"
    good.write_text("version = 1\n[run]\nmax_cost = 0.5\n")
    assert load_budget(good) == Budget(max_cost=0.5)
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 1\n[run]\nmax_dollars = 1\n")
    with pytest.raises(ValueError, match="max_calls, max_tokens, max_cost"):
        load_budget(bad)
    bad.write_text("version = 1\n[run]\n")
    with pytest.raises(ValueError, match="at least one cap"):
        load_budget(bad)


async def test_a_model_call_past_the_cap_is_never_made() -> None:
    agent = LLMAgent([_reply(), _reply(), _reply()])  # wants three calls
    app = build_supervisor(
        [agent], load_policy(), budget=Budget(max_calls=2), prices=PRICES
    ).compile(checkpointer=_saver())

    with pytest.raises(BudgetExceeded, match="next call would cross max_calls: 3 > 2"):
        await app.ainvoke(initial_ops_state(make_task()), CFG)

    remaining = list(agent.model.messages)  # the third reply was never consumed
    assert len(remaining) == 1


async def test_a_run_already_over_budget_doesnt_start_the_agent() -> None:
    agent = ScriptedAgent([("publish_public", "medium")])
    app = build_supervisor(
        [agent], load_policy(), budget=Budget(max_tokens=100), prices=PRICES
    ).compile(checkpointer=_saver())
    state = initial_ops_state(make_task())
    state["usage"] = [Usage(agent="echo", model="model-a", input_tokens=90, output_tokens=20)]

    with pytest.raises(BudgetExceeded, match="max_tokens: 110 > 100"):
        await app.ainvoke(state, CFG)
    assert agent.runs == 0


async def test_unknown_cost_stops_the_run_under_a_cost_cap() -> None:
    model = GenericFakeChatModel(messages=iter([_reply("model-unpriced")] * 2))
    agent = LLMAgent([])
    agent.model, agent.calls = model, 2
    app = build_supervisor(
        [agent], load_policy(), budget=Budget(max_cost=10.0), prices=PRICES
    ).compile(checkpointer=_saver())

    with pytest.raises(BudgetExceeded, match="cost unknown"):
        await app.ainvoke(initial_ops_state(make_task()), CFG)


async def test_within_budget_nothing_changes() -> None:
    agent = LLMAgent([_reply(), _reply()], action_type="publish_public")
    app = build_supervisor(
        [agent], load_policy(), budget=Budget(max_calls=2, max_cost=1.0), prices=PRICES
    ).compile(checkpointer=_saver())
    out = await app.ainvoke(initial_ops_state(make_task()), CFG)
    assert len(out["usage"]) == 2
    assert "__interrupt__" in out
