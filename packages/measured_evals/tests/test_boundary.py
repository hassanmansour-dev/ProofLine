"""measured_evals must stay installable without the proofline app or measured_ops (ADR-0012, ADR-0014)."""

from __future__ import annotations

import ast
from importlib.metadata import requires, version
from pathlib import Path

import measured_evals

SRC = Path(measured_evals.__file__).resolve().parent


def test_version_comes_from_package_metadata() -> None:
    assert measured_evals.__version__ == version("measured-evals")


FORBIDDEN = ("proofline", "measured_ops")


def test_does_not_depend_on_the_apps() -> None:
    deps = requires("measured-evals") or []
    assert not [d for d in deps if d.lower().replace("-", "_").startswith(FORBIDDEN)]


def test_source_never_imports_the_apps() -> None:
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
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in FORBIDDEN]
    assert offenders == []
