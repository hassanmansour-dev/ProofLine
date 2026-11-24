"""measured_ops never imports the proofline app (ADR-0014): agents are injected."""

from __future__ import annotations

import ast
from importlib.metadata import requires, version
from pathlib import Path

import measured_ops

SRC = Path(measured_ops.__file__).resolve().parent


def test_version_comes_from_package_metadata() -> None:
    assert measured_ops.__version__ == version("measured-ops")


def test_does_not_depend_on_proofline() -> None:
    deps = requires("measured-ops") or []
    assert not [d for d in deps if d.lower().startswith("proofline")]


def test_source_never_imports_proofline() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] == "proofline"]
    assert offenders == []


def test_demo_policy_ships_with_the_package() -> None:
    assert (SRC / "config" / "approval_policy.demo.toml").is_file()


def test_demo_tools_config_ships_with_the_package() -> None:
    assert (SRC / "config" / "tools.demo.toml").is_file()


def test_the_base_package_never_imports_mcp() -> None:
    # `mcp` is the optional `tools` extra (C5, ADR-0022): the contracts, the supervisor,
    # the policy and the executor layer must import without it.
    import subprocess
    import sys

    code = (
        "import sys; import measured_ops, measured_ops.supervisor, measured_ops.tools, "
        "measured_ops.tools.config; assert 'mcp' not in sys.modules, 'mcp imported'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_tests_never_see_the_developers_runtime_config() -> None:
    # Review: with these exported in a shell, tests could write real Postgres logs,
    # run under a real budget or start real MCP servers. The root conftest clears them.
    import os

    for name in ("MEASURED_OPS_LOGS_URL", "MEASURED_OPS_BUDGET_PATH", "MEASURED_OPS_TOOLS_CONFIG"):
        assert name not in os.environ, name


def test_tests_cannot_open_postgres_logs_outside_the_test_database() -> None:
    import pytest

    from measured_ops.pg_logs import PostgresJudgeLog, PostgresPostedLog

    for cls in (PostgresJudgeLog, PostgresPostedLog):
        with pytest.raises(AssertionError, match="real Postgres logs"):
            cls("postgresql://someone@db.example.com/ops")
