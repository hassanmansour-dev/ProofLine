"""finalizer — Draft → FinalScript + files. Deterministic, no LLM.

Contract (docs/ARCHITECTURE.md §4):
    reads:   draft, research_notes, critique?, revision_count
    writes:  final_script
    thread:  runtime.execution_info.thread_id (required: outputs are keyed by it)
Renders the Markdown script (title, hook, body, call to action, cited sources) and
writes outputs/<thread_id>/script.md and script.json, overwriting on every run so a
retried or resumed finalizer is idempotent.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.prompts.loader import prompt_versions
from proofline.render import script_markdown
from proofline.schemas import FinalScript
from proofline.state import ScriptState

log = logging.getLogger("proofline.nodes.finalizer")


def _write_outputs(out_dir: Path, final: FinalScript) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.md").write_text(final.markdown, encoding="utf-8")
    (out_dir / "script.json").write_text(final.model_dump_json(indent=2), encoding="utf-8")


async def finalizer(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    info = runtime.execution_info
    thread_id = info.thread_id if info is not None else None
    if not thread_id:
        raise ValueError("finalizer: no thread_id in config; outputs are keyed by it")
    draft = state["draft"]
    notes = state["research_notes"]
    cited = set(draft.cited_source_ids)

    final = FinalScript(
        thread_id=thread_id,
        title=draft.title,
        markdown=script_markdown(draft, notes),
        word_count=draft.word_count,
        estimated_seconds=draft.estimated_seconds,
        revision_count=state.get("revision_count", 0),
        final_score=critique.overall if (critique := state.get("critique")) else None,
        source_urls=[s.url for s in notes.sources if s.id in cited],
        prompt_versions=prompt_versions(),
    )
    out_dir = runtime.context.settings.output_dir / thread_id
    # Overwrite on every run, so a retried or resumed finalizer is idempotent.
    await asyncio.to_thread(_write_outputs, out_dir, final)

    log.info(
        "finalizer: wrote %s (%d words, score %s)", out_dir, final.word_count, final.final_score
    )
    return {"final_script": final}
