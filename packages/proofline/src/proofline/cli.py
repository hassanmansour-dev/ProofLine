"""CLI: `proofline run --topic ... --audience ...` prints the finished script.

It runs the pipeline graph (with its critic loop) with no checkpointer. With LangSmith
tracing on, traces go through the PII-masked tracer (ADR-0018). There is no review prompt: approval
happens in the supervisor's gate and inbox (C2, C4; ADR-0014).

`.env` is loaded first, from the working directory (the same file Settings reads):
pydantic-settings only reads PROOFLINE_* into Settings, but the Anthropic, Tavily and
LangSmith SDKs read their keys from the process environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv

from measured_ops.tracing import masked_tracing_callbacks
from proofline.config import get_settings
from proofline.context import Deps, build_deps
from proofline.graph import build_graph, invoke_config, new_thread_id
from proofline.schemas import FinalScript
from proofline.state import initial_state

app = typer.Typer(no_args_is_help=True, help="Proofline: topic → sourced short script.")


async def run_pipeline(topic: str, audience: str, deps: Deps) -> FinalScript:
    """One run: planner → researcher → writer ⇄ critic → finalizer."""
    settings = deps.settings
    graph = build_graph().compile()
    # PII-masked LangSmith tracer when tracing is on, replacing LangChain's default one
    # (ADR-0018); [] when it's off.
    cfg = invoke_config(new_thread_id(), settings)
    cfg["callbacks"] = masked_tracing_callbacks()
    out = await graph.ainvoke(
        initial_state(topic, audience, settings.run_config()), cfg, context=deps
    )
    final: FinalScript = out["final_script"]
    return final


@app.command()
def run(
    topic: Annotated[str, typer.Option(help="What the script is about")],
    audience: Annotated[str, typer.Option(help="Who it's for")] = "developers new to AI",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="log each node")] = False,
) -> None:
    """Run the pipeline on a topic and print the finished script as Markdown."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(find_dotenv(usecwd=True))
    try:
        settings = get_settings()
        settings.run_config()  # validate bounds now, not mid-run
        deps = build_deps(settings)
    except (ValueError, RuntimeError) as exc:  # bad settings, unknown provider, missing key
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if settings.researcher == "stub":
        typer.echo(
            "note: research is stubbed (PROOFLINE_RESEARCHER=stub): fixed sample notes, "
            "not research on this topic",
            err=True,
        )
    final = asyncio.run(run_pipeline(topic, audience, deps))
    typer.echo(final.markdown)
    out_dir = deps.settings.output_dir / final.thread_id
    typer.echo(f"saved to {out_dir}/script.md and script.json", err=True)


@app.command()
def show(thread_id: str) -> None:
    """Print a run's state and history from the checkpoint."""
    raise NotImplementedError("C2 — needs the checkpointer")


def _deps_or_exit() -> Deps:
    """Load .env, settings and dependencies; exit 1 with the reason if they're unusable."""
    load_dotenv(find_dotenv(usecwd=True))
    try:
        settings = get_settings()
        settings.run_config()  # validate bounds now, not mid-run
        return build_deps(settings)
    except (ValueError, RuntimeError) as exc:  # bad settings, unknown provider, missing key
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


async def propose_topic(topic: str, audience: str, deps: Deps) -> tuple[str, bool, str]:
    """Start one content-agent run in the inbox's database (ADR-0019).

    Returns (thread id, paused at the gate, the agent's summary or error)."""
    from measured_ops import Task, initial_ops_state
    from measured_ops.checkpoint import open_checkpointer
    from measured_ops.inbox.settings import (
        checkpointer_url,
        env_path,
        revision_timeout_from_env,
    )
    from measured_ops.judge_log import judge_log_from_env
    from measured_ops.policy import load_policy
    from measured_ops.supervisor import build_supervisor
    from proofline.agent import ContentAgent

    agent = ContentAgent(deps, judge_log=judge_log_from_env())
    task = Task(
        agent=agent.name,
        goal="Draft a LinkedIn post, a 1-minute clip script and a lesson outline.",
        inputs={"topic": topic, "audience": audience},
    )
    thread_id = new_thread_id()
    policy = load_policy(env_path("MEASURED_OPS_POLICY_PATH"))
    async with open_checkpointer(checkpointer_url()) as saver:
        graph = build_supervisor([agent], policy).compile(checkpointer=saver)
        timeout = revision_timeout_from_env()
        try:
            # The inbox's revision timeout: a run still going here is cut off before the
            # inbox (stale only after twice that) would offer to retry it (ADR-0019).
            out = await asyncio.wait_for(
                graph.ainvoke(
                    initial_ops_state(task),
                    {
                        "configurable": {"thread_id": thread_id},
                        "recursion_limit": 40,
                        "callbacks": masked_tracing_callbacks(),  # PII-masked (ADR-0018)
                    },
                ),
                timeout=timeout,
            )
        except TimeoutError:
            return thread_id, False, f"timed out after {timeout:g}s"
    result = out["results"][-1]
    return thread_id, bool(out.get("__interrupt__")), result.error or result.summary


@app.command()
def propose(
    topic: Annotated[str, typer.Option(help="What the post is about")],
    audience: Annotated[str, typer.Option(help="Who it's for")] = "developers new to AI",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="log each node")] = False,
) -> None:
    """Draft one topic with the content agent; the draft waits in the inbox for approval.

    Until C6 creates tasks, this is how a topic gets in. Uses the inbox's database
    (MEASURED_OPS_INBOX_CHECKPOINTER_URL, docs/INBOX.md). Never publishes (ADR-0019).
    """
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    deps = _deps_or_exit()
    from measured_ops.killswitch import KillSwitchEngaged

    try:
        thread_id, paused, summary = asyncio.run(propose_topic(topic, audience, deps))
    except KillSwitchEngaged as exc:
        typer.echo(f"error: {exc}; nothing ran", err=True)
        raise typer.Exit(code=1) from exc
    if not paused:
        typer.echo(f"error: no draft is waiting: {summary}", err=True)
        raise typer.Exit(code=1)
    typer.echo(summary, err=True)
    typer.echo(f"waiting in the inbox: {thread_id}")


async def ready_markdown() -> str:
    """Approved, not-yet-posted drafts as Markdown, newest first (ADR-0020). Reads only."""
    from measured_ops.checkpoint import open_checkpointer
    from measured_ops.inbox.service import Inbox
    from measured_ops.inbox.settings import checkpointer_url, env_path
    from measured_ops.policy import load_policy
    from measured_ops.supervisor import build_supervisor

    policy = load_policy(env_path("MEASURED_OPS_POLICY_PATH"))
    async with open_checkpointer(checkpointer_url()) as saver:
        graph = build_supervisor([], policy).compile(checkpointer=saver)
        items = await Inbox(graph, policy, callbacks=[]).list_ready()
    if not items:
        return "Nothing is ready to paste.\n"
    parts: list[str] = []
    for item in items:
        parts.append(
            f"# {item.goal}\n\n*approved {item.approved_at[:16].replace('T', ' ')}"
            f" · run {item.thread_id} · action {item.action_id}*\n"
        )
        parts += [f"## {title}\n\n{text}\n" for title, text in item.blocks]
        parts.append("---\n")
    return "\n".join(parts)


@app.command()
def ready(
    out: Annotated[
        Path | None, typer.Option(help="Also write the Markdown to this file, under outputs/")
    ] = None,
) -> None:
    """Print approved drafts not yet marked as posted, as Markdown (newest first).

    Reads the inbox's database; changes nothing. Copy from here, or from the inbox's
    Ready to paste page; mark them as posted there.
    """
    load_dotenv(find_dotenv(usecwd=True))
    settings = get_settings()
    if out is not None:
        root = settings.output_dir.resolve()
        target = out.resolve()
        if not target.is_relative_to(root):
            typer.echo(f"error: --out must be under {settings.output_dir}/ (git-ignored)", err=True)
            raise typer.Exit(code=1)
    markdown = asyncio.run(ready_markdown())
    typer.echo(markdown, nl=False)
    if out is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        typer.echo(f"saved to {out}", err=True)


@app.command()
def golden(
    out: Annotated[Path, typer.Option(help="Directory for <item-id>.md files, under outputs/")],
    item: Annotated[
        list[str] | None, typer.Option("--item", help="Only these item ids (repeatable)")
    ] = None,
    all_items: Annotated[
        bool, typer.Option("--all", help="Include items not approved yet (not for baselines)")
    ] = False,
    overwrite: Annotated[bool, typer.Option(help="Run items already saved again")] = False,
) -> None:
    """Run the real pipeline on the golden set and save one output per item.

    Each item uses its own fixture sources (no search) and real models: this costs money.
    Then check the outputs with `python -m measured_evals run --approved-only --outputs OUT`.
    Items already saved are skipped, so a stopped run resumes where it left off.

    If MEASURED_OPS_BUDGET_PATH names a budget, it caps each item, not the whole run:
    20 items can spend up to 20 times the cap. The kill switch stops the run between items.
    """
    from measured_evals.golden import load_golden_set
    from measured_ops.budget import budget_from_env
    from measured_ops.killswitch import KillSwitch, KillSwitchEngaged
    from measured_ops.prices import load_model_prices
    from proofline.golden import CRITIC_FILE, run_golden

    deps = _deps_or_exit()
    switch = KillSwitch.from_env()
    budget = budget_from_env()  # MEASURED_OPS_BUDGET_PATH: caps each item when set
    prices_path = os.environ.get("MEASURED_OPS_PRICES_PATH", "").strip()
    prices = load_model_prices(Path(prices_path) if prices_path else None)
    root = deps.settings.output_dir.resolve()
    if not out.resolve().is_relative_to(root):
        typer.echo(
            f"error: --out must be under {deps.settings.output_dir}/ (git-ignored)", err=True
        )
        raise typer.Exit(code=1)
    golden_set = load_golden_set()
    try:
        result = asyncio.run(
            run_golden(
                golden_set,
                deps,
                out,
                approved_only=not all_items,
                only=set(item) if item else None,
                overwrite=overwrite,
                progress=lambda item_id, what: typer.echo(f"{item_id}: {what}", err=True),
                budget=budget,
                prices=prices,
                kill_switch=switch,
            )
        )
    except KillSwitchEngaged as exc:
        typer.echo(f"error: {exc}; saved items are kept, run again after release", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"saved {len(result.written)}, skipped {len(result.skipped)}, failed {len(result.failed)} "
        f"(golden set {golden_set.name}) in {out}/, critic scores in {out}/{CRITIC_FILE}"
    )
    for item_id, error in result.failed.items():
        typer.echo(f"FAILED {item_id}: {error}", err=True)
    typer.echo(
        "next: uv run python -m measured_evals run --approved-only "
        f"--outputs {out} --write-draft-baseline evals/baselines/content_v0.json"
    )
    if result.failed:
        raise typer.Exit(code=1)


@app.command()
def inbox() -> None:
    """Serve the approval inbox with the content agent (127.0.0.1, one process).

    Revisions run the content agent in this process (ADR-0019), so it needs the same
    keys as `proofline run`. Settings: docs/INBOX.md. The demo echo agent is served too,
    so runs from `python -m measured_ops.inbox seed` still resume.
    """
    from measured_ops.agent import Agent
    from measured_ops.echo import EchoAgent
    from measured_ops.inbox import server  # needs measured-ops[inbox]
    from measured_ops.judge_log import judge_log_from_env
    from proofline.agent import ContentAgent

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    deps = _deps_or_exit()
    judge_log = judge_log_from_env()  # one log: critic lines and decisions (ADR-0019)
    agents: list[Agent] = [ContentAgent(deps, judge_log=judge_log), EchoAgent()]
    raise typer.Exit(code=server.serve(agents, judge_log=judge_log))


@app.command()
def mermaid() -> None:
    """Print the graph as mermaid."""
    print(build_graph().compile().get_graph().draw_mermaid())


if __name__ == "__main__":
    app()
