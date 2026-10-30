"""PreToolUse(Edit|Write|MultiEdit): keep secrets, lockfiles and data files out of reach."""

from __future__ import annotations

from pathlib import PurePath

from _common import pre_tool_decision, project_dir, read_input


def reason_for(path: str) -> str | None:
    p = PurePath(path)
    name = p.name
    parts = set(p.parts)
    if name.startswith(".env") and name != ".env.example":
        return f"{name} holds secrets. Edit .env.example and let Hassan copy values."
    if name == "uv.lock":
        return "uv.lock is generated. Change pyproject via `uv add` / `uv remove`."
    if p.suffix in {".db", ".sqlite", ".sqlite3"} or ".data" in parts:
        return "Checkpoint databases are runtime data, not source."
    if "outputs" in parts:
        return "outputs/ is written by the finalizer at runtime."
    if ".git" in parts:
        return "Never edit .git internals."
    return None


def main() -> None:
    data = read_input()
    file_path = str(data.get("tool_input", {}).get("file_path", ""))
    if not file_path:
        return
    try:
        rel = str(PurePath(file_path).relative_to(project_dir()))
    except ValueError:
        rel = file_path
    reason = reason_for(rel)
    if reason:
        pre_tool_decision("deny", reason)


if __name__ == "__main__":
    main()
