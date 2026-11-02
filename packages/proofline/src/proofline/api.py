"""FastAPI app. Week 3 S3. Contract in docs/ARCHITECTURE.md §7.

`create_app(deps=..., checkpointer_url=...)` is the seam tests use (fakes + memory://);
`app = create_app()` is what uvicorn serves with real deps from .env.

Implementation notes for Week 3:
- lifespan: dotenv.load_dotenv(); open_checkpointer(url) once; compile build_graph() once;
  build_deps() unless injected; store on app.state; close on shutdown.
  (httpx ASGITransport doesn't run lifespan — tests enter app.router.lifespan_context(app).)
- POST /runs (StartRunRequest): thread_id = new_thread_id(); ainvoke(initial_state(...),
  invoke_config(...), context=deps) → returns at the interrupt → RunView(status="awaiting_review").
- GET /runs/{thread_id}: aget_state; 404 if the thread has no checkpoint (snapshot.values empty);
  status: snapshot.interrupts → awaiting_review; "final_script" in values → completed; else running.
- POST /runs/{thread_id}/review (HumanDecision): 404 unknown, 409 unless awaiting_review;
  ainvoke(Command(resume=decision.model_dump()), ...) → RunView.
- Keep handlers thin: a small `RunService` (graph + deps + settings) does the work.
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

    # Week 3 S3: lifespan + /runs endpoints go here.
    return api


app = create_app()
