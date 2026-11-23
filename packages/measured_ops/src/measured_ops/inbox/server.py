"""Run the inbox: `serve(agents)` (C4, ADR-0017; shared by `proofline inbox` in C8).

127.0.0.1 only and one worker: the phone reaches it through `tailscale serve`, never
`funnel` (docs/INBOX.md), and decision locks and background revisions live in this
one process (the lock file enforces it). The agents must be the ones whose runs the
inbox resumes: a revision runs the agent in this process (ADR-0019).

Environment: `MEASURED_OPS_INBOX_*` (settings.py), `MEASURED_OPS_POLICY_PATH`,
`MEASURED_OPS_PRICES_PATH` (demo tables when unset), `MEASURED_OPS_TOOLS_CONFIG` (C5: the
tools config whose executors run approved actions; no tools when unset, and then an
executable action fails loudly instead of being dropped).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

from measured_ops.agent import Agent
from measured_ops.inbox.settings import InboxConfigError, InboxSettings, checkpointer_url, env_path
from measured_ops.inbox.web import create_inbox_app
from measured_ops.judge_log import JudgeLog
from measured_ops.policy import load_policy
from measured_ops.prices import load_model_prices
from measured_ops.tools.errors import ToolConfigError
from measured_ops.tools.executors import Executors

HOST = "127.0.0.1"  # never 0.0.0.0: the phone reaches it over Tailscale only


def serve(
    agents: Sequence[Agent],
    *,
    eval_context: Mapping[str, object] | None = None,
    judge_log: JudgeLog | None = None,
    executors: Executors | None = None,
) -> int:
    """Run the inbox until stopped. Returns 2 if the settings are unusable.

    `executors` default to the ones `MEASURED_OPS_TOOLS_CONFIG` describes (none if unset)."""
    import uvicorn

    checkpointer_url()
    try:
        settings = InboxSettings.from_env()
        if executors is None:
            executors = _executors_from_env()
    except (InboxConfigError, ToolConfigError) as exc:
        print(f"inbox: {exc}", file=sys.stderr)  # noqa: T201 (CLI entry point)
        return 2
    app = create_inbox_app(
        settings,
        agents,
        load_policy(env_path("MEASURED_OPS_POLICY_PATH")),
        prices=load_model_prices(env_path("MEASURED_OPS_PRICES_PATH")),
        eval_context=eval_context,
        judge_log=judge_log,
        executors=executors,
    )
    uvicorn.run(app, host=HOST, port=settings.port, workers=1, proxy_headers=False)
    return 0


def _executors_from_env() -> Executors | None:
    from measured_ops.tools.config import tools_config_from_env

    config = tools_config_from_env()
    if config is None:
        return None
    from measured_ops.tools.mcp import build_executors  # needs the `tools` extra

    return build_executors(config)
