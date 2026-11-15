"""The inbox service (C4, ADR-0017): list runs paused at the gate and decide them, with
no web code. In-memory checkpointer, strict msgpack."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from ops_fakes import ScriptedAgent, make_task

from measured_ops import ApprovalPolicy, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.inbox.service import AlreadyDecided, Inbox, Refused
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio

APPROVAL = ("publish_public", "medium")
AUTO = ("draft_content", "low")


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _saver() -> InMemorySaver:
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))


def _graph(saver: InMemorySaver, *agents: Any, policy: ApprovalPolicy | None = None) -> Any:
    return build_supervisor(list(agents), policy or load_policy()).compile(checkpointer=saver)


def _cfg(thread: str) -> Any:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 40}


async def _start(graph: Any, thread: str, goal: str = "Echo the topic back.") -> None:
    await graph.ainvoke(initial_ops_state(make_task(goal=goal)), _cfg(thread))


class SpyGraph:
    """Records every ainvoke input, so tests can prove the inbox never resumes with None."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.inputs: list[Any] = []

    async def ainvoke(self, value: Any, config: Any = None, **kwargs: Any) -> Any:
        self.inputs.append(value)
        return await self._graph.ainvoke(value, config, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


# ---- listing ----------------------------------------------------------------------------


async def test_lists_only_runs_paused_at_the_gate_oldest_first() -> None:
    saver = _saver()
    await _start(_graph(saver, ScriptedAgent([APPROVAL])), "first", goal="First goal.")
    await _start(_graph(saver, ScriptedAgent([AUTO])), "auto-only")
    await _start(_graph(saver, ScriptedAgent(fail=True)), "failed")
    await _start(_graph(saver, ScriptedAgent([APPROVAL])), "second", goal="Second goal.")
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy())

    items = await inbox.list_pending()

    assert [i.thread_id for i in items] == ["first", "second"]
    first = items[0]
    assert (first.goal, first.agent, first.type, first.target) == (
        "First goal.",
        "echo",
        "publish_public",
        "linkedin",
    )
    assert first.preview == "publish_public #0"
    assert first.risk_level == "medium"
    assert not first.blocked_by_policy
    assert {"action_id", "approved"} <= set(first.response_schema["required"])


async def test_get_returns_none_for_a_thread_not_waiting() -> None:
    saver = _saver()
    await _start(_graph(saver, ScriptedAgent([AUTO])), "done")
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy())

    assert await inbox.get("done") is None
    assert await inbox.get("never-existed") is None


# ---- deciding ---------------------------------------------------------------------------


async def test_approve_resumes_the_thread_and_it_leaves_the_inbox() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()

    outcome = await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
    )

    assert (outcome.approved, outcome.still_paused) == (True, False)
    assert await inbox.list_pending() == []
    state = (await graph.aget_state(_cfg("t"))).values
    assert [a.id for a in state["ready"]] == [item.action_id]


async def test_reject_records_the_feedback_and_ends_the_run() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()

    await inbox.decide(
        "t",
        interrupt_id=item.interrupt_id,
        action_id=item.action_id,
        approved=False,
        feedback="Too long.",
    )

    state = (await graph.aget_state(_cfg("t"))).values
    [decision] = state["decisions"]
    assert (decision.approved, decision.feedback) == (False, "Too long.")
    assert state["ready"] == []
    assert await inbox.list_pending() == []


async def test_blank_feedback_is_stored_as_none() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()

    await inbox.decide(
        "t",
        interrupt_id=item.interrupt_id,
        action_id=item.action_id,
        approved=False,
        feedback="   ",
    )

    state = (await graph.aget_state(_cfg("t"))).values
    assert state["decisions"][0].feedback is None


async def test_a_run_with_two_actions_comes_back_with_the_next_one() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL, APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [first] = await inbox.list_pending()

    outcome = await inbox.decide(
        "t", interrupt_id=first.interrupt_id, action_id=first.action_id, approved=True
    )
    [second] = await inbox.list_pending()

    assert outcome.still_paused
    assert second.thread_id == "t"
    assert second.action_id != first.action_id
    assert second.preview == "publish_public #1"


# ---- refusals and double taps -----------------------------------------------------------


async def test_refused_answer_keeps_the_item_and_a_new_answer_works() -> None:
    saver = _saver()
    spy = SpyGraph(_graph(saver, ScriptedAgent([APPROVAL])))
    await _start(spy, "t")
    inbox = Inbox(spy, load_policy())
    [item] = await inbox.list_pending()

    with pytest.raises(Refused, match="not the action waiting"):
        await inbox.decide(
            "t", interrupt_id=item.interrupt_id, action_id="someone-else", approved=True
        )

    [still] = await inbox.list_pending()
    assert still.interrupt_id == item.interrupt_id
    await inbox.decide("t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True)
    assert await inbox.list_pending() == []
    resumes = spy.inputs[1:]  # the first input started the run
    assert resumes
    assert all(isinstance(r, Command) for r in resumes)  # never ainvoke(None)


async def test_feedback_over_the_limit_is_refused_without_resuming() -> None:
    saver = _saver()
    spy = SpyGraph(_graph(saver, ScriptedAgent([APPROVAL])))
    await _start(spy, "t")
    inbox = Inbox(spy, load_policy())
    [item] = await inbox.list_pending()

    with pytest.raises(Refused):
        await inbox.decide(
            "t",
            interrupt_id=item.interrupt_id,
            action_id=item.action_id,
            approved=False,
            feedback="x" * 4001,
        )

    assert len(spy.inputs) == 1  # only the start; nothing was sent to the gate
    assert len(await inbox.list_pending()) == 1


async def test_a_second_tap_on_the_same_item_is_already_decided() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL, APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()
    kwargs: dict[str, Any] = {
        "interrupt_id": item.interrupt_id,
        "action_id": item.action_id,
        "approved": True,
    }

    await inbox.decide("t", **kwargs)

    # The run is paused again, on the next action: the old interrupt id must not answer it.
    with pytest.raises(AlreadyDecided):
        await inbox.decide("t", **kwargs)
    state = (await graph.aget_state(_cfg("t"))).values
    assert len(state["decisions"]) == 1


async def test_two_taps_at_once_decide_once() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()

    async def tap() -> str:
        try:
            await inbox.decide(
                "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
            )
        except AlreadyDecided:
            return "already"
        return "decided"

    results = await asyncio.gather(tap(), tap())

    assert sorted(results) == ["already", "decided"]
    state = (await graph.aget_state(_cfg("t"))).values
    assert len(state["decisions"]) == 1


async def test_deciding_a_thread_that_isnt_waiting_is_already_decided() -> None:
    inbox = Inbox(_graph(_saver(), ScriptedAgent()), load_policy())

    with pytest.raises(AlreadyDecided):
        await inbox.decide("nope", interrupt_id="i", action_id="a", approved=True)


# ---- a policy tightened to "never" after the pause --------------------------------------


async def test_action_now_never_is_listed_as_blocked_and_only_rejecting_ends_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saver = _saver()
    demo = load_policy()
    await _start(_graph(saver, ScriptedAgent([APPROVAL]), policy=demo), "t")
    strict = ApprovalPolicy(version=1, actions={**demo.actions, "publish_public": "never"})
    graph = _graph(saver, ScriptedAgent(), policy=strict)
    inbox = Inbox(graph, strict)

    with caplog.at_level("WARNING", logger="measured_ops"):
        [item] = await inbox.list_pending()

    assert item.blocked_by_policy  # shown, not hidden: hiding would leave it paused forever
    assert any("publish_public" in r.getMessage() for r in caplog.records)
    with pytest.raises(Refused, match="never allowed"):
        await inbox.decide(
            "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
        )
    assert len(await inbox.list_pending()) == 1

    outcome = await inbox.decide(
        "t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=False
    )

    assert not outcome.still_paused
    assert await inbox.list_pending() == []
    state = (await graph.aget_state(_cfg("t"))).values
    assert state["ready"] == []
    assert [d.approved for d in state["decisions"]] == [False]


# ---- resilience -------------------------------------------------------------------------


async def test_one_unreadable_thread_doesnt_break_the_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "good")
    await _start(graph, "bad")

    class Flaky(Inbox):
        async def get(self, thread_id: str) -> Any:
            if thread_id == "bad":
                raise KeyError("risk_level")
            return await super().get(thread_id)

    with caplog.at_level("ERROR", logger="measured_ops.inbox"):
        items = await Flaky(graph, load_policy()).list_pending()

    assert [i.thread_id for i in items] == ["good"]
    assert any("bad" in r.getMessage() for r in caplog.records)


async def test_decision_locks_dont_pile_up() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()

    for thread in ("nope-1", "nope-2"):
        with pytest.raises(AlreadyDecided):
            await inbox.decide(thread, interrupt_id="i", action_id="a", approved=True)
    await inbox.decide("t", interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True)

    assert inbox._locks == {}


async def test_refusal_reasons_read_plainly() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "t")
    inbox = Inbox(graph, load_policy())
    [item] = await inbox.list_pending()

    with pytest.raises(Refused) as refused:
        await inbox.decide("t", interrupt_id=item.interrupt_id, action_id="x", approved=True)

    assert str(refused.value).startswith("decision is for action x")


# ---- eval scores and cost on each waiting action (C7, ADR-0018) -------------------------


async def test_waiting_action_shows_its_checks_with_reasons() -> None:
    saver = _saver()
    leaky = ("draft_content", "low", "Mail jane.doe@example.com: https://example.com/?a=1")
    await _start(_graph(saver, ScriptedAgent([leaky])), "t")
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy())

    [item] = await inbox.list_pending()

    assert item.eval is not None
    assert item.eval.passed is False
    assert item.eval.evaluator == "guard-v0"
    assert [(c.name, c.passed, c.reason) for c in item.eval.checks] == [
        ("quality", True, None),
        ("injection", False, "link with query parameters"),
        ("pii", False, "email found"),
        ("target_injection", True, None),
        ("target_pii", True, None),
    ]


async def test_waiting_action_shows_cost_per_agent() -> None:
    from langchain_core.messages import AIMessage
    from ops_fakes import LLMAgent

    from measured_evals import ModelPrice

    reply = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000},
        response_metadata={"model_name": "model-a"},
    )
    saver = _saver()
    await _start(_graph(saver, LLMAgent([reply], action_type="publish_public")), "t")
    prices = {
        "model-a": ModelPrice(
            input=4, output=20, cache_read=0.2, cache_write_5m=5, cache_write_1h=8
        )
    }
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy(), prices=prices)

    [item] = await inbox.list_pending()

    assert [(c.agent, c.tokens, c.cost) for c in item.costs] == [("echo", 1_000_000, 4.0)]


async def test_unpriced_model_shows_cost_unknown() -> None:
    from langchain_core.messages import AIMessage
    from ops_fakes import LLMAgent

    reply = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
        response_metadata={"model_name": "not-in-the-table"},
    )
    saver = _saver()
    await _start(_graph(saver, LLMAgent([reply], action_type="publish_public")), "t")
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy())

    [item] = await inbox.list_pending()

    assert [(c.tokens, c.cost) for c in item.costs] == [(10, None)]


async def test_run_paused_before_c7_has_no_eval_and_still_lists() -> None:
    saver = _saver()
    graph = _graph(saver, ScriptedAgent([APPROVAL]))
    await _start(graph, "old")
    # simulate a pre-C7 pause: the gate payload had no "eval" key
    snap = await graph.aget_state(_cfg("old"))
    payload = dict(snap.interrupts[0].value)
    payload.pop("eval")
    inbox = Inbox(graph, load_policy())

    values = {k: v for k, v in snap.values.items() if k not in ("usage", "evaluated_actions")}
    item = inbox._item("old", snap._replace(values=values), payload)

    assert item.eval is None
    assert item.costs is None  # not tracked, which is not the same as free


async def test_run_without_model_calls_has_empty_costs_not_none() -> None:
    saver = _saver()
    await _start(_graph(saver, ScriptedAgent([APPROVAL])), "t")
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy())

    [item] = await inbox.list_pending()

    assert item.costs == ()


async def test_a_long_haiku_call_is_priced_at_the_long_context_rate() -> None:
    from langchain_core.messages import AIMessage
    from ops_fakes import LLMAgent

    def reply(prompt: int) -> AIMessage:
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": prompt, "output_tokens": 0, "total_tokens": prompt},
            response_metadata={"model_name": "claude-haiku-5-5"},
        )

    saver = _saver()
    agent = LLMAgent([reply(100_000), reply(100_001)], action_type="publish_public")
    await _start(_graph(saver, agent), "t")
    inbox = Inbox(_graph(saver, ScriptedAgent()), load_policy())  # demo prices

    [item] = await inbox.list_pending()

    # 100,000 at $0.10/MTok + 100,001 at $0.50/MTok, each call priced on its own
    assert item.costs is not None
    [(agent_name, tokens, dollars)] = [(c.agent, c.tokens, c.cost) for c in item.costs]
    assert agent_name == "echo"
    assert tokens == 200_001
    assert dollars == pytest.approx(0.01 + 0.0500005)


async def test_resumes_pass_the_tracing_callbacks() -> None:
    from langchain_core.callbacks import BaseCallbackHandler

    saver = _saver()
    await _start(_graph(saver, ScriptedAgent([APPROVAL])), "t")
    spy = SpyGraph(_graph(saver, ScriptedAgent()))
    marker = BaseCallbackHandler()
    configs: list[Any] = []
    original = spy.ainvoke

    async def ainvoke(value: Any, config: Any = None, **kwargs: Any) -> Any:
        configs.append(config)
        return await original(value, config, **kwargs)

    spy.ainvoke = ainvoke  # type: ignore[method-assign]
    inbox = Inbox(spy, load_policy(), callbacks=[marker])
    [item] = await inbox.list_pending()

    await inbox.decide(
        item.thread_id, interrupt_id=item.interrupt_id, action_id=item.action_id, approved=True
    )

    assert configs[-1]["callbacks"] == [marker]
