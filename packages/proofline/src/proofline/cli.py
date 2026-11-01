"""CLI. `run` lands in Week 1 S5 (--no-review), streaming in Week 2 S3, review in Week 3 S1.

Design notes for the implementation:
- Call `dotenv.load_dotenv()` first: pydantic-settings only reads PROOFLINE_* into
  Settings, but the Anthropic, Tavily and LangSmith SDKs read their keys from the
  process environment. (api.py does the same in its lifespan.)
- `run`: thread_id = new_thread_id(); open_checkpointer(settings.checkpointer_url);
  compile; astream(initial_state(...), invoke_config(...), context=build_deps(),
  stream_mode="updates"); print one rich line per node update (node, key numbers).
- On "__interrupt__": show the payload, ask approve? [y/N], feedback if no;
  resume with Command(resume=HumanDecision(...).model_dump()) — same thread_id,
  same context; loop until final_script.
- `show THREAD_ID`: aget_state + aget_state_history → table of history records.
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, help="Build Clearly script pipeline")


@app.command()
def run(
    topic: Annotated[str, typer.Argument(help="What the video is about")],
    audience: Annotated[
        str, typer.Option(help="Who it's for")
    ] = "developers moving into AI engineering",
    video_format: Annotated[str, typer.Option("--format", help="short | long")] = "short",
    no_review: Annotated[
        bool, typer.Option("--no-review", help="skip human review (Weeks 1-2)")
    ] = False,
) -> None:
    """Run the pipeline on a topic."""
    raise NotImplementedError("Week 1 S5 — see module docstring")


@app.command()
def show(thread_id: str) -> None:
    """Print a run's state and history from the checkpoint."""
    raise NotImplementedError("Week 3 S2 — see module docstring")


@app.command()
def mermaid() -> None:
    """Print the graph as mermaid."""
    from proofline.graph import build_graph

    print(build_graph().compile().get_graph().draw_mermaid())


if __name__ == "__main__":
    app()
