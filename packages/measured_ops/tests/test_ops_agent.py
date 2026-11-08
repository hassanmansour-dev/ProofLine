"""Agent interface: a subgraph that takes {"task"} and returns only {"result"}."""

from __future__ import annotations

import pytest
from ops_fakes import (
    EchoAgent,
    LeakyAgent,
    NoOutputSchemaAgent,
    SelfScoringAgent,
    make_task,
)

from measured_ops import Agent, AgentContractError, AgentResult, check_agent_graph, run_agent

pytestmark = pytest.mark.anyio


def test_echo_agent_satisfies_the_protocol() -> None:
    assert isinstance(EchoAgent(), Agent)


def test_conforming_graph_passes_the_check() -> None:
    check_agent_graph(EchoAgent().build())


def test_graph_without_schemas_fails_the_check() -> None:
    with pytest.raises(AgentContractError, match="input_schema=AgentInput"):
        check_agent_graph(LeakyAgent().build())


def test_graph_without_output_schema_fails_the_check() -> None:
    with pytest.raises(AgentContractError, match="output_schema=AgentOutput"):
        check_agent_graph(NoOutputSchemaAgent().build())


async def test_run_agent_rejects_self_scored_actions() -> None:
    agent = SelfScoringAgent()
    with pytest.raises(AgentContractError, match="scored its own actions"):
        await run_agent(agent, agent.build(), make_task())


async def test_output_is_only_the_result() -> None:
    task = make_task()
    out = await EchoAgent().build().ainvoke({"task": task})
    assert set(out) == {"result"}  # the agent's private "text" field doesn't leak


async def test_run_agent_returns_the_result_for_the_task() -> None:
    agent = EchoAgent()
    task = make_task(goal="Say hi.")
    result = await run_agent(agent, agent.build(), task)
    assert isinstance(result, AgentResult)
    assert result.task_id == task.id
    assert result.status == "succeeded"
    assert [a.preview for a in result.actions] == ["Draft: Say hi."]


async def test_run_agent_refuses_a_task_for_another_agent() -> None:
    agent = EchoAgent()
    with pytest.raises(AgentContractError, match="not 'echo'"):
        await run_agent(agent, agent.build(), make_task(agent="content"))


async def test_run_agent_rejects_a_graph_with_no_result() -> None:
    agent = LeakyAgent()
    with pytest.raises(AgentContractError, match="no AgentResult"):
        await run_agent(agent, agent.build(), make_task(agent="leaky"))


async def test_run_agent_rejects_a_result_for_another_task() -> None:
    class Wrong(EchoAgent):
        name = "wrong"

    agent = Wrong()  # its graph answers as "echo", not "wrong"
    with pytest.raises(AgentContractError, match="expected task"):
        await run_agent(agent, agent.build(), make_task(agent="wrong"))
