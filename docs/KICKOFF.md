# Kickoff — from zip to first Claude Code session (~30 min)

## 1. Prerequisites
- `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`), `git`, `gh` (optional)
- `python3` on PATH — the Claude Code hooks are stdlib Python scripts
- Claude Code installed and signed in
- API keys: Anthropic, Tavily (free tier is enough for Phase 1), LangSmith (optional until Week 4)

## 2. Create the repo
```bash
mkdir proofline && cd proofline
unzip ~/Downloads/proofline.zip -d .      # or wherever you saved it
git init -b main
uv sync                                                 # creates .venv + uv.lock
cp .env.example .env                                    # fill in the two required keys
uv run pytest -m "not live"                             # expect: all pass, 18 xfailed
git add -A && git commit -m "chore: project kickoff kit"
gh repo create proofline --private --source . --push   # private until the Jan 24 launch
```

## 3. First Claude Code session
```bash
claude
```
- Approve the project MCP server `docs-langchain` when prompted (LangChain's official
  docs server, used to verify LangGraph APIs).
- Model: use **Opus** for this first session (design review) — `/model`. Implementation
  sessions can run on Sonnet; the reviewer, architect and prompt-engineer agents are
  pinned to Opus regardless.
- You should see the session-start context (current week, branch, status).

Paste this as the first message:

```text
You're joining Proofline as the senior engineer on my flagship build (Phase 1: LangGraph fundamentals).
Read CLAUDE.md, docs/ARCHITECTURE.md, docs/PLAN.md and every ADR in docs/adr/.

Then, without writing code:
1. Use the langgraph-architect agent to review the contracts that are already
   implemented (state.py, schemas.py, routing.py, render.py, llm.py, prompts/loader.py,
   checkpointing.py) against the architecture and ADRs. I want real disagreements,
   not a summary — anything you'd change before we lock them.
2. Use the prompt-engineer agent to review the four prompts in src/proofline/prompts/
   against docs/PROMPTING.md and docs/RUBRIC.md. Flag that docs/BRAND_VOICE.md is
   still TODO if it is.
3. Run the fast suite and confirm the xfail targets per week.
4. Give me: the top 5 changes you recommend before Week 1 (ranked, each with
   why + cost), and what Week 1 S1 should look like.

Wait for my answers before changing anything.
```

## 4. Daily loop (each session)
1. `/week N` → see where the week stands and the next session's tasks
2. `/new-node <name>` for node work (test-first, prompt review, wiring, review)
3. Commit when `/ship-check` passes
4. `/live-run "topic"` when you want to see real output (paid calls; it asks first)

## 5. What's in the kit
| Path | What it does |
|---|---|
| `CLAUDE.md` | Project memory: scope rule, commands, architecture, standards, DoD |
| `.claude/settings.json` | Permissions (allow/ask/deny) + hook wiring |
| `.claude/hooks/` | Bash guard, file protection, ruff-on-edit, prompt-version check, test-on-stop, session context |
| `.claude/agents/` | `langgraph-architect`, `code-reviewer`, `test-engineer`, `prompt-engineer`, `api-verifier` |
| `.claude/skills/` | `/week`, `/new-node`, `/ship-check`, `/adr`, `/live-run`, `/readme`, + `langgraph-patterns` reference |
| `.mcp.json` | LangChain docs MCP server (dev tooling only — the app itself has no MCP in Phase 1) |
| `docs/` | Architecture, 10 ADRs, 13-week plan, rubric, prompt standards, brand voice (yours), monthly template |
| `src/proofline/` | Contracts + infra implemented; nodes, graph wiring, CLI, API are your Week 1–3 work |
| `tests/` | 68 passing foundation tests + 18 `xfail(strict=True)` week targets |

## 6. Turning things off
- Skip test-on-stop for a session: `PROOFLINE_SKIP_STOP_CHECKS=1 claude`
- Personal overrides: `.claude/settings.local.json` (gitignored)
