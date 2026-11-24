"""Errors of the tool layer (C5, ADR-0022). Messages name tools, operations and fields,
never values read from a tool (ticket summaries, event titles) or secrets."""

from __future__ import annotations


class ToolError(RuntimeError):
    """A tool answered, but with an error or a result we can't use. Not retried."""


class ToolUnavailableError(ToolError):
    """A tool couldn't be reached or timed out. Transient: the execute node retries it."""


class ToolConfigError(ToolError):
    """The tools config is unusable: missing file, unknown server, unset env var."""


class ToolNotAllowedError(PermissionError):
    """A client was asked for a tool outside its allowlist (e.g. a write on a read client)."""


class ScopeError(PermissionError):
    """An action targets something outside the configured scope (another Jira project,
    a calendar that isn't in the config). Never executed."""
