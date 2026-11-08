"""measured_ops — contracts, approval policy and (from C2) the supervisor (ADR-0014).

Never imports the proofline app: agents are passed in, not imported.
"""

from importlib.metadata import version

from measured_ops.agent import (
    Agent,
    AgentContractError,
    AgentGraph,
    AgentInput,
    AgentOutput,
    check_agent_graph,
    run_agent,
)
from measured_ops.contracts import (
    AgentResult,
    ApprovalDecision,
    OpsState,
    PendingAction,
    RiskLevel,
    Task,
    initial_ops_state,
)
from measured_ops.policy import ActionBlockedError, ApprovalPolicy, Decision, load_policy

__version__ = version("measured-ops")

__all__ = [
    "ActionBlockedError",
    "Agent",
    "AgentContractError",
    "AgentGraph",
    "AgentInput",
    "AgentOutput",
    "AgentResult",
    "ApprovalDecision",
    "ApprovalPolicy",
    "Decision",
    "OpsState",
    "PendingAction",
    "RiskLevel",
    "Task",
    "__version__",
    "check_agent_graph",
    "initial_ops_state",
    "load_policy",
    "run_agent",
]
