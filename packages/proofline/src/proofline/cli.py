"""CLI: `proofline run --topic ... --audience ...` prints the finished script.

Week 1 runs the linear graph with no checkpointer. Week 2 streams node updates;
Week 3 adds the review prompt (interrupt + resume) and `show THREAD_ID`.

`.env` is loaded first, from the working directory (the same file Settings reads):
pydantic-settings only reads PROOFLINE_* into Settings, but the Anthropic, Tavily and
LangSmith SDKs read their keys from the process environment.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv

from proofline.config import get_settings
from proofline.context import Deps, build_deps
from proofline.graph import build_graph, invoke_config, new_thread_id
from proofline.schemas import FinalScript
from proofline.state import initial_state

app = typer.Typer(no_args_is_help=True, help="Proofline: topic → sourced short script.")


async def run_pipeline(topic: str, audience: str, deps: Deps) -> FinalScript:
    """One linear run: planner → researcher → writer → finalizer."""
    settings = deps.settings
    graph = build_graph().compile()
    out = await graph.ainvoke(
        initial_state(topic, audience, settings.run_config()),
        invoke_config(new_thread_id(), settings),
        context=deps,
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
    raise NotImplementedError("Week 3 — needs the checkpointer")


@app.command()
def mermaid() -> None:
    """Print the graph as mermaid."""
    print(build_graph().compile().get_graph().draw_mermaid())


if __name__ == "__main__":
    app()
