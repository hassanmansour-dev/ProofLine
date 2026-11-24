"""Run the real pipeline on the golden set and save what `measured_evals run` reads.

`docs/EVALS.md` ("Making it real", step 2) needs one output per approved item, saved as
`<item-id>.md`, before `python -m measured_evals run --approved-only --outputs DIR` has
anything to check. This is that step:

- each item runs the full pipeline (planner → researcher → writer ⇄ critic → finalizer)
  with the item's own fixture sources (`FixtureResearcher`), its topic and its audience;
- the output is the final draft in the shape the checks split on: `# title`, then the
  hook, the body and the call to action, separated by blank lines, citations as written,
  no Sources list (it isn't spoken and would count as words);
- the critic's overall score per item goes to `critic_scores.json`, the file
  `python -m measured_evals calibration-sheet --critic-scores` reads;
- items already saved are skipped unless `overwrite`, so a stopped run resumes without
  paying twice; one item failing is reported and the others still run.

It spends money, so the same two stops as a supervisor run apply (ADR-0023): the kill
switch is checked before every item (engaged mid-run, the remaining items don't start),
and a budget, when one is set, caps each item's calls, tokens and cost before every model
call; an item over its cap is reported as failed and the next one still runs.

It calls real models (keys, money): only Hassan runs it for real. Tests use fake models.
Outputs go under `outputs/` (git-ignored): they are measurements, not fixtures.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from measured_evals.budget import Budget
from measured_evals.cost import Prices
from measured_evals.golden import GoldenItem, GoldenSet
from measured_ops.budget import guard_budget
from measured_ops.killswitch import KillSwitch
from measured_ops.tracing import masked_tracing_callbacks
from measured_ops.usage import collect_usage
from proofline.context import Deps
from proofline.graph import build_graph, invoke_config, new_thread_id
from proofline.research.fixture import FixtureResearcher
from proofline.schemas import Draft
from proofline.state import initial_state

log = logging.getLogger("proofline.golden")

CRITIC_FILE = "critic_scores.json"


def script_text(draft: Draft) -> str:
    """The draft as `measured_evals` reads an output: title, hook, body, call to action."""
    return (
        "\n\n".join(
            [
                f"# {draft.title.strip()}",
                draft.hook.strip(),
                draft.body.strip(),
                draft.call_to_action.strip(),
            ]
        )
        + "\n"
    )


@dataclass
class GoldenRun:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # item id -> error type and message


def _save(out: Path, target: Path, text: str, critic: dict[str, object]) -> None:
    target.write_text(text, encoding="utf-8")
    (out / CRITIC_FILE).write_text(json.dumps(critic, indent=2) + "\n", encoding="utf-8")


def _prepare(out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    return _critic_file(out)


def _critic_file(out: Path) -> dict[str, object]:
    path = out / CRITIC_FILE
    if path.exists():
        document: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        return document
    return {"scores": {}, "scale": [1, 10], "synthetic": False}


async def run_item(
    item: GoldenItem, deps: Deps, *, budget: Budget | None = None, prices: Prices | None = None
) -> tuple[str, float | None]:
    """One item through the pipeline: (output text, critic overall score). With `budget`,
    every model call is checked against it first (BudgetExceeded stops the item)."""
    item_deps = replace(deps, researcher=FixtureResearcher(item.id, item.sources, deps.llm))
    cfg = invoke_config(new_thread_id(), deps.settings)
    cfg["callbacks"] = masked_tracing_callbacks()
    cfg.setdefault("metadata", {})["golden_item"] = item.id
    state = initial_state(item.topic, item.audience, deps.settings.run_config())
    graph = build_graph().compile()
    if budget is None:
        out = await graph.ainvoke(state, cfg, context=item_deps)
    else:
        with (
            collect_usage("golden") as usage,
            guard_budget(budget, prices or {}, [], usage),
        ):
            out = await graph.ainvoke(state, cfg, context=item_deps)
    score = out.get("score")
    return script_text(out["draft"]), None if score is None else float(score)


async def run_golden(
    golden: GoldenSet,
    deps: Deps,
    out: Path,
    *,
    approved_only: bool = True,
    only: set[str] | None = None,
    overwrite: bool = False,
    progress: Callable[[str, str], None] | None = None,
    budget: Budget | None = None,
    prices: Prices | None = None,
    kill_switch: KillSwitch | None = None,
) -> GoldenRun:
    """Run the items one at a time (cost and rate limits); save each as it finishes.
    Raises KillSwitchEngaged if the kill switch is on before an item starts."""
    switch = kill_switch or KillSwitch.from_env()
    items = golden.approved if approved_only else golden.items
    if only:
        unknown = sorted(only - {i.id for i in golden.items})
        if unknown:
            raise ValueError(f"no golden item(s) {unknown}")
        items = tuple(i for i in items if i.id in only)
    critic = await asyncio.to_thread(_prepare, out)
    scores = critic["scores"]
    assert isinstance(scores, dict)
    run = GoldenRun()
    for item in items:
        target = out / f"{item.id}.md"
        if not overwrite and await asyncio.to_thread(target.exists):
            run.skipped.append(item.id)
            if progress:
                progress(item.id, "skipped (already saved)")
            continue
        switch.check()  # before every item: nothing more is spent once it's engaged
        try:
            text, score = await run_item(item, deps, budget=budget, prices=prices)
        except Exception as exc:  # reported, and the next item still runs
            log.exception("golden: item %s failed", item.id)
            run.failed[item.id] = f"{type(exc).__name__}: {exc}"[:300]
            if progress:
                progress(item.id, "FAILED")
            continue
        if score is not None:
            scores[item.id] = score
        critic["source"] = (
            f"proofline golden run; writer {deps.settings.writer_model}, "
            f"critic {deps.settings.critic_model}"
        )
        await asyncio.to_thread(_save, out, target, text, critic)
        run.written.append(item.id)
        if progress:
            progress(item.id, "saved")
    return run
