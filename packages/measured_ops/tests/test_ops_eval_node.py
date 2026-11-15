"""The eval/guard node (C7, ADR-0018): call_agent → evaluate → triage → gate.

Scores come only from the eval node, a failed check only raises "auto" to approval,
no matched PII value appears in logs or in the gate payload outside the preview, and
usage is counted per agent with prompt-cache tokens apart from normal input.
Demo data only (example.com, 555-01xx).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from ops_fakes import LLMAgent, ScriptedAgent, make_task

from measured_evals import EvalScores, Usage
from measured_ops import ApprovalDecision, initial_ops_state, load_policy
from measured_ops.contracts import SERDE_ALLOWLIST
from measured_ops.supervisor import build_supervisor

pytestmark = pytest.mark.anyio

EMAIL = "jane.doe@example.com"
PHONE = "(514) 555-0123"
LEAKY = f"Reach me at {EMAIL} or {PHONE}."


@pytest.fixture(autouse=True)
def _strict_serde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")


def _app(*agents: Any, **kwargs: Any) -> Any:
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SERDE_ALLOWLIST))
    return build_supervisor(list(agents), load_policy(), **kwargs).compile(checkpointer=saver)


def _cfg() -> Any:
    return {"configurable": {"thread_id": "t"}, "recursion_limit": 40}


async def _start(app: Any) -> dict[str, Any]:
    out: dict[str, Any] = await app.ainvoke(initial_ops_state(make_task()), _cfg())
    return out


# ---- scoring ------------------------------------------------------------------------


async def test_clean_auto_action_is_scored_and_ready() -> None:
    out = await _start(_app(ScriptedAgent([("draft_content", "low", "A clean draft.")])))

    assert "__interrupt__" not in out
    [ready] = out["ready"]
    assert isinstance(ready.eval_scores, EvalScores)
    assert ready.eval_scores.evaluator == "guard-v0"
    assert ready.eval_scores.passed


async def test_the_agents_own_result_stays_unscored() -> None:
    out = await _start(_app(ScriptedAgent([("draft_content", "low", "A clean draft.")])))

    assert [a.eval_scores for a in out["results"][-1].actions] == [None]
    assert [a.eval_scores is not None for a in out["evaluated_actions"]] == [True]


async def test_failed_check_raises_an_auto_action_to_approval() -> None:
    out = await _start(_app(ScriptedAgent([("draft_content", "low", LEAKY)])))

    [intr] = out["__interrupt__"]
    assert intr.value["type"] == "draft_content"
    assert intr.value["eval"] == {
        "evaluator": "guard-v0",
        "passed": False,
        "failing": ["pii"],
        "scores": {
            "quality": 1.0,
            "injection": 1.0,
            "pii": 0.0,
            "target_injection": 1.0,
            "target_pii": 1.0,
        },
        "reasons": {"pii": "email found, phone number found"},
    }
    assert out["ready"] == []


async def test_passed_checks_never_lower_an_approval_row() -> None:
    out = await _start(_app(ScriptedAgent([("publish_public", "low", "A clean draft.")])))

    [intr] = out["__interrupt__"]
    assert intr.value["eval"]["passed"] is True


async def test_never_action_stays_blocked_whatever_the_scores() -> None:
    out = await _start(_app(ScriptedAgent([("bulk_export_personal_data", "low", "clean")])))

    assert [a.type for a in out["blocked"]] == ["bulk_export_personal_data"]
    assert out["blocked"][0].eval_scores.passed


async def test_eval_context_comes_from_the_supervisor() -> None:
    app = _app(
        ScriptedAgent([("draft_content", "low", f"Questions: {EMAIL}")]),
        eval_context={"pii_allow": [EMAIL]},
    )

    out = await _start(app)

    assert "__interrupt__" not in out
    assert out["ready"][0].eval_scores.passed


async def test_a_crashing_check_fails_the_run_loudly() -> None:
    async def broken(output: str, context: Any) -> EvalScores:
        raise RuntimeError("guard broke")

    app = _app(ScriptedAgent([("draft_content", "low", "clean")]), evaluator=broken)

    with pytest.raises(RuntimeError, match="guard broke"):
        await _start(app)


async def test_scores_survive_the_checkpoint_and_the_resume() -> None:
    app = _app(ScriptedAgent([("draft_content", "low", LEAKY)]))
    out = await _start(app)
    action_id = out["__interrupt__"][0].value["action_id"]

    decision = ApprovalDecision(action_id=action_id, approved=True)
    done = await app.ainvoke(Command(resume=decision.model_dump()), _cfg())

    [ready] = done["ready"]
    assert ready.eval_scores.reasons == {"pii": "email found, phone number found"}


# ---- no PII value outside the preview -----------------------------------------------


async def test_no_matched_value_in_logs_or_the_payload_outside_the_preview(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        out = await _start(_app(ScriptedAgent([("draft_content", "low", LEAKY)])))
    payload = dict(out["__interrupt__"][0].value)
    assert payload.pop("preview") == LEAKY  # the human still sees what they approve

    for value in (EMAIL, PHONE, "jane.doe", "555-0123"):
        assert value not in caplog.text
        assert value not in repr(payload)
        assert value not in repr(out["evaluated_actions"][0].eval_scores)


# ---- usage and tracing ----------------------------------------------------------------


def _reply(**usage: Any) -> AIMessage:
    return AIMessage(
        content="ok", usage_metadata=usage, response_metadata={"model_name": "model-a"}
    )


async def test_usage_is_counted_per_agent_with_cache_tokens_apart() -> None:
    first = _reply(
        input_tokens=1000,  # langchain-anthropic: includes cache reads and writes
        output_tokens=50,
        total_tokens=1050,
        input_token_details={
            "cache_read": 600,
            "cache_creation": 0,
            "ephemeral_5m_input_tokens": 100,
            "ephemeral_1h_input_tokens": 50,
        },
    )
    second = _reply(input_tokens=200, output_tokens=20, total_tokens=220)

    out = await _start(_app(LLMAgent([first, second])))

    assert out["usage"] == [
        Usage(
            agent="echo",
            model="model-a",
            input_tokens=250 + 200,
            output_tokens=70,
            cache_read_tokens=600,
            cache_write_5m_tokens=100,
            cache_write_1h_tokens=50,
        )
    ]


async def test_unsplit_cache_writes_count_as_five_minute_writes() -> None:
    reply = _reply(
        input_tokens=300,
        output_tokens=1,
        total_tokens=301,
        input_token_details={"cache_read": 0, "cache_creation": 100},
    )

    out = await _start(_app(LLMAgent([reply])))

    [usage] = out["usage"]
    assert (usage.input_tokens, usage.cache_write_5m_tokens) == (200, 100)


async def test_a_call_without_a_model_name_is_counted_as_unknown() -> None:
    reply = AIMessage(
        content="ok", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}
    )

    out = await _start(_app(LLMAgent([reply])))

    assert [(u.model, u.input_tokens) for u in out["usage"]] == [("unknown", 5)]


async def test_agent_runs_are_tagged_for_tracing() -> None:
    agent = LLMAgent([])
    task = make_task()

    await _app(agent).ainvoke(initial_ops_state(task), _cfg())

    assert "agent:echo" in agent.seen_config["tags"]
    assert agent.seen_config["metadata"]["agent"] == "echo"
    assert agent.seen_config["metadata"]["task_id"] == task.id


async def test_target_is_checked_for_injection_and_pii() -> None:
    target = f"https://search.example/?q={EMAIL}"
    out = await _start(_app(ScriptedAgent([("research", "low", "Look this up.", target)])))

    [intr] = out["__interrupt__"]  # research is "auto": raised by the target alone
    assert intr.value["eval"]["failing"] == ["target_injection", "target_pii"]
    assert intr.value["eval"]["reasons"] == {
        "target_injection": "link with query parameters",
        "target_pii": "email found",
    }


async def test_usage_of_a_failed_agent_run_is_logged_before_it_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ops_fakes import LLMAgent

    class Boom(LLMAgent):
        def build(self) -> Any:
            graph = super().build()
            agent = self

            class Wrapped:
                def __getattr__(self, name: str) -> Any:
                    return getattr(graph, name)

                async def ainvoke(self, *a: Any, **k: Any) -> Any:
                    await agent.model.ainvoke("hi")
                    raise RuntimeError("agent broke")

            return Wrapped()  # type: ignore[return-value]

    app = _app(Boom([_reply(input_tokens=40, output_tokens=2, total_tokens=42)]))

    with (
        caplog.at_level(logging.WARNING, logger="measured_ops.supervisor"),
        pytest.raises(RuntimeError, match="agent broke"),
    ):
        await _start(app)

    assert "agent 'echo' failed after using" in caplog.text
    assert "model-a" in caplog.text
    assert "42 tokens" in caplog.text
