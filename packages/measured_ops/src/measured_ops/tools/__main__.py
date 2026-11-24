"""`python -m measured_ops.tools {check,list}` (C5, ADR-0022). Needs the `tools` extra.

    check [--config PATH]               verify a tools config against each server's tool list:
                                        every mapped tool exists, every mapped argument is one
                                        of its parameters, required parameters are covered, and
                                        read tools aren't marked as writing
    list --server NAME [--config PATH]  print a server's tools and their parameters, to fill in
                                        a config's mapping

Both only list tools (`tools/list`): they call no tool, read no record and change nothing.
Without --config they use MEASURED_OPS_TOOLS_CONFIG, else the public demo config.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import mcp_types

from measured_ops.tools.config import (
    OpMap,
    ToolsConfig,
    load_tools_config,
    tools_config_from_env,
)
from measured_ops.tools.errors import ToolError
from measured_ops.tools.mcp import McpToolClient


def _ops(config: ToolsConfig) -> list[tuple[str, str, OpMap, bool]]:
    """(section.op, server, mapping, is_write) for every mapped operation."""
    out: list[tuple[str, str, OpMap, bool]] = []
    if config.jira:
        t = config.jira.tools
        for name, op, write in (
            ("search", t.search, False),
            ("get_issue", t.get_issue, False),
            ("transitions", t.transitions, False),
            ("transition", t.transition, True),
            ("update", t.update, True),
        ):
            out.append((f"jira.{name}", config.jira.server, op, write))
    if config.calendar:
        c = config.calendar.tools
        out.append(("calendar.list_events", config.calendar.server, c.list_events, False))
        out.append(("calendar.create_event", config.calendar.server, c.create_event, True))
    return out


def check_config(
    config: ToolsConfig, listings: Mapping[str, Sequence[mcp_types.Tool]]
) -> tuple[list[str], list[str]]:
    """Pure: (problems, ok lines) for `config` against each server's tool list."""
    problems: list[str] = []
    ok: list[str] = []
    for label, server, op, write in _ops(config):
        tools = {t.name: t for t in listings.get(server, ())}
        tool = tools.get(op.tool)
        if tool is None:
            problems.append(f"{label}: server {server!r} has no tool {op.tool!r}")
            continue
        schema = tool.input_schema or {}
        params = set(schema.get("properties", {}))
        required = set(schema.get("required", []))
        given = {a.split(".", 1)[0] for a in op.args.values()} | set(op.const)
        unknown = sorted(given - params)
        missing = sorted(required - given)
        if unknown:
            problems.append(f"{label}: {op.tool} has no parameter(s) {unknown}")
        if missing:
            problems.append(f"{label}: {op.tool} requires {missing}, not mapped")
        hints = tool.annotations
        if not write and hints is not None and hints.read_only_hint is False:
            problems.append(f"{label}: {op.tool} says it isn't read-only; reads must not write")
        if not unknown and not missing:
            ok.append(f"ok  {label} -> {server}.{op.tool} ({'write' if write else 'read'})")
    return problems, ok


async def _listings(config: ToolsConfig) -> dict[str, list[mcp_types.Tool]]:
    servers = {server for _, server, _, _ in _ops(config)}
    out: dict[str, list[mcp_types.Tool]] = {}
    for server in sorted(servers):
        client = McpToolClient(server, config.servers[server], frozenset())
        out[server] = await client.list_tools()
    return out


def _config(path: str | None) -> ToolsConfig:
    if path:
        return load_tools_config(Path(path))
    return tools_config_from_env() or load_tools_config()


def cmd_check(path: str | None) -> int:
    config = _config(path)
    problems, ok = check_config(config, asyncio.run(_listings(config)))
    for line in ok:
        print(line)
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    return 1 if problems else 0


def cmd_list(path: str | None, server: str) -> int:
    config = _config(path)
    if server not in config.servers:
        print(f"no server {server!r} in the config", file=sys.stderr)
        return 2
    tools = asyncio.run(McpToolClient(server, config.servers[server], frozenset()).list_tools())
    for tool in sorted(tools, key=lambda t: t.name):
        schema = tool.input_schema or {}
        required = set(schema.get("required", []))
        params = ", ".join(
            f"{p}{'' if p in required else '?'}" for p in schema.get("properties", {})
        )
        hints = tool.annotations
        mode = (
            ""
            if hints is None or hints.read_only_hint is None
            else (" [read-only]" if hints.read_only_hint else " [writes]")
        )
        print(f"{tool.name}({params}){mode}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m measured_ops.tools")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="verify a tools config against the servers")
    check.add_argument("--config")
    listing = sub.add_parser("list", help="print a server's tools and parameters")
    listing.add_argument("--server", required=True)
    listing.add_argument("--config")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return cmd_check(args.config)
        return cmd_list(args.config, args.server)
    except ToolError as exc:
        print(f"tools: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
