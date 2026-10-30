"""finalizer — approved Draft → FinalScript + files. Week 1 S4. Deterministic, no LLM.

Contract (docs/ARCHITECTURE.md §4):
    reads:   draft, research_notes, critique, revision_count
    writes:  final_script
    thread:  runtime.execution_info.thread_id (verified on langgraph 1.2.16; the
             (state, config: RunnableConfig, runtime) signature also works).
Steps:
    1. markdown = render.script_markdown(draft, research_notes)
    2. final = FinalScript(thread_id=..., title=draft.title, markdown=markdown,
                           word_count=draft.word_count, estimated_seconds=draft.estimated_seconds,
                           revision_count=..., final_score=critique.overall,
                           source_urls=[urls of cited sources], prompt_versions=prompt_versions())
    3. Write settings.output_dir / thread_id / "script.md" and "script.json"
       (final.model_dump_json(indent=2)). Overwrite = idempotent on retry.
       File I/O: wrap in asyncio.to_thread (no blocking I/O in async nodes).
    4. return {"final_script": final}
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from proofline.context import Deps
from proofline.state import ScriptState


async def finalizer(state: ScriptState, runtime: Runtime[Deps]) -> dict[str, Any]:
    raise NotImplementedError("Week 1 S4 — see module docstring")
