"""measured_evals must stay installable without the proofline app (ADR-0012)."""

from __future__ import annotations

import ast
from importlib.metadata import requires, version
from pathlib import Path

import measured_evals

SRC = Path(measured_evals.__file__).resolve().parent


def test_version_comes_from_package_metadata() -> None:
    assert measured_evals.__version__ == version("measured-evals")


def test_does_not_depend_on_proofline() -> None:
    deps = requires("measured-evals") or []
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
