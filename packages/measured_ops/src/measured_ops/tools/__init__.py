"""Measured Ops tool layer (C5, ADR-0022): read ports for agents, executors for writes.

Light on purpose: nothing here imports `mcp`. The MCP adapters (`measured_ops.tools.mcp`),
the demo servers and the CLI need the `tools` extra.
"""

from measured_ops.tools.errors import (
    ScopeError,
    ToolConfigError,
    ToolError,
    ToolNotAllowedError,
    ToolUnavailableError,
)
from measured_ops.tools.executors import Executors, execute_action
from measured_ops.tools.specs import (
    CALENDAR_BLOCK,
    SPECS,
    TRIAGE,
    BlockParams,
    ToolScope,
    TriageParams,
)

__all__ = [
    "CALENDAR_BLOCK",
    "SPECS",
    "TRIAGE",
    "BlockParams",
    "Executors",
    "ScopeError",
    "ToolConfigError",
    "ToolError",
    "ToolNotAllowedError",
    "ToolScope",
    "ToolUnavailableError",
    "TriageParams",
    "execute_action",
]
