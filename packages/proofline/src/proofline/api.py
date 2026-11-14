"""FastAPI app: /health only (ADR-0014).

The run/review endpoints this module once planned are dropped: the content pipeline
becomes the content agent (C8), and the supervisor's approval gate (C2) and the
approval inbox (C4, `measured_ops.inbox`, ADR-0017) handle runs and approval.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from proofline.config import get_settings
from proofline.context import Deps


def create_app(*, deps: Deps | None = None, checkpointer_url: str | None = None) -> FastAPI:
    api = FastAPI(title="proofline", version="0.0.1")
    url = checkpointer_url or (deps.settings if deps else get_settings()).checkpointer_url

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "checkpointer": url.split(":", 1)[0]}

    return api


app = create_app()
