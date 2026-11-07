# Proofline

[![CI](https://github.com/hassanmansour-dev/ProofLine/actions/workflows/ci.yml/badge.svg)](https://github.com/hassanmansour-dev/ProofLine/actions/workflows/ci.yml)

**An eval-gated, multi-agent content pipeline built with LangGraph. Nothing ships
without passing the evals and a human sign-off.**

Give Proofline a topic and an audience. It plans an angle, researches it, writes a
short technical script with inline citations, and formats the result: a title, a
hook, a body and a call to action, under 200 words. The same script works as a
LinkedIn post and as a 1-minute video about AI engineering.

Built in public. **Proofline v1 is Measured Ops** ([ADR-0013](docs/adr/0013-measured-ops-is-proofline-v1.md)):
a LangGraph supervisor with specialist agents that runs a real work plan, with a
human approving anything public, any deploy, any spend and any message to a real
person. This content pipeline is its first slice. Today the linear flow works end to
end; the [roadmap](#roadmap) shows what comes next, in order.

## How it works (Week 1)

```mermaid
flowchart LR
    START([topic + audience]) --> planner --> researcher --> writer --> finalizer --> END([Markdown script])
```

| Node | Does | Output |
|---|---|---|
| **planner** | Picks one angle for this audience; writes answerable questions, search queries and 2–4 beats | `Outline` (structured output) |
| **researcher** | Calls whatever `Researcher` is configured: fixed notes this week, a web-search agent in the content agent's v1 | `ResearchNotes` with source ids `S1..Sn` assigned in code |
| **writer** | Writes the script from the outline and notes; every claim cites a source; asks itself for one cut if it runs over the limit | `Draft` (title, hook, body, call to action) |
| **finalizer** | Renders Markdown with the cited sources; saves `outputs/<run-id>/script.md` and `script.json` | `FinalScript` |

Things a reviewer may want to check:
- **Every node gets its dependencies from LangGraph's runtime context** (an LLM port
  and a `Researcher` port), never from globals. The whole graph runs in tests with a
  fake model in milliseconds.
- **Derived numbers are computed in code, never asked of the model:** word count,
  cited sources, and later the critic's overall score.
- **Source ids come from code, and citations to ids that don't exist are dropped.**
  Web content reaches the model escaped, inside tags, as data and never as
  instructions.
- **Prompts are versioned files** with a changelog (`packages/proofline/src/proofline/prompts/`).
- **Decisions are recorded** as ADRs in [`docs/adr/`](docs/adr/).

## Two packages, one repo

```
packages/
  proofline/        the pipeline app: graph, nodes, CLI (API from C2)
  measured_evals/   the eval + security layer (interface in C1; metrics and CI later)
```

The evaluation and security code lives in its own installable package,
`measured-evals`, from day one, and **it never imports Proofline**:

- **It's reused.** A separate web app (Measured Lab) will sit on the same
  package, and it will be published as open source. Code that knows about
  Proofline's schemas can't be either.
- **Evals have to be independent of what they judge.** A metric that reaches into
  the pipeline's internals ends up grading itself.
- **Splitting later is expensive.** Starting split costs one `pyproject.toml`;
  untangling the imports in Week 10 would cost a week.

The boundary is enforced, not just intended. `packages/measured_evals/tests/test_boundary.py`
fails if the package declares or imports `proofline`, and CI installs
`measured_evals` on its own, without the app, and runs its tests. Dependencies point
one way: `proofline` → `measured_evals`. Details: [ADR-0012](docs/adr/0012-workspace-split-and-week1-reset.md).

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync                                   # installs both packages + dev tools
cp .env.example .env                      # add ANTHROPIC_API_KEY
uv run proofline run --topic "Why every AI loop needs an exit" --audience "developers new to AI"
```

The script prints to stdout and is saved under `outputs/<run-id>/`. Add `-v` to see
one log line per node. `uv run proofline mermaid` prints the graph.

Configuration comes from environment variables (see [`.env.example`](.env.example)):

| Variable | Default | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for real runs |
| `PROOFLINE_LLM_PROVIDER` | `anthropic` | Model provider (adapters live in `llm.py`) |
| `PROOFLINE_PLANNER_MODEL`, `_WRITER_MODEL`, … | Haiku / Sonnet | Model per node role |
| `PROOFLINE_BRAND_NAME` | `Build Clearly` | Brand the scripts are written for |
| `PROOFLINE_MAX_WORDS` | `200` | Word limit for the script |
| `PROOFLINE_RESEARCHER` | `stub` | `stub` = fixed notes, no network; `web` = Tavily search (needs `TAVILY_API_KEY`) |

The voice the writer follows lives in [`docs/BRAND_VOICE.md`](docs/BRAND_VOICE.md).

## Develop

```bash
uv run pytest                 # both packages; live tests skip without API keys
uv run pytest -m live         # real model + search calls (costs money)
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict, on both packages
```

Unit tests never touch the network: nodes run inside a real one-node graph with a
fake model, and their updates are read back from a checkpointer, so serialization
problems show up in tests. CI runs ruff, mypy and pytest on both packages on every
push and pull request.

## Roadmap

Ordered steps, no dates. Each starts when the steps it depends on are done.

| Step | Milestone |
|---|---|
| ✅ | Linear flow, two-package workspace (`proofline` + `measured_evals`), CLI, CI |
| **C1** ◀ now | Contract: shared state, agent interface, approval actions, eval interface |
| C0 | One-command bootstrap (repo, database, hosting, env) and the private ops repo |
| C2 | Supervisor skeleton with checkpointer, approval interrupt, end-to-end test in CI |
| C3–C7 | Approval inbox (mobile), MCP tools, orchestrator, eval/guard node with tracing and cost |
| C8 | Content agent: one topic → post, clip script, lesson outline, scored and waiting for approval |
| C9–C10 | A week of real use, then scheduled runs with budget caps and a kill switch |
| C11 | More agents, one at a time, same recipe |
| Evals | Golden set, DeepEval + promptfoo regression checks in CI, injection suite, budgets, auth |
| Launch | Case study, demo video, `measured-evals` on PyPI · `v1.0.0` |

Step detail: [`docs/PLAN.md`](docs/PLAN.md). Architecture:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Repository layout

```
packages/proofline/src/proofline/   state, schemas, nodes/, research/, prompts/, graph, cli
packages/proofline/tests/           unit tests (fakes) + live tests
packages/measured_evals/            standalone eval + security package
docs/                               plan, architecture, ADRs, rubric, prompting standards
.claude/                            Claude Code setup: hooks, review agents, skills
```

Built by Hassan Mansour on his own time, as a public portfolio project.
