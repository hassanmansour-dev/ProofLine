"""The inbox web app (C4, ADR-0017): FastAPI, server-rendered, phone first, no JavaScript.

    GET|HEAD /login, POST /login, POST /logout
    POST /r/{thread_id}/retry    continue a stopped revision (C8, ADR-0019)
    GET  /ready                  approved drafts to paste; POST /ready/{id}/posted (ADR-0020)
    GET  /                       actions waiting at the gate
    GET  /a/{thread_id}          one action: preview, approve / reject with feedback
    POST /a/{thread_id}/decision resume the run with the answer

Safety, in the order a request meets it:
- one process only (`single_process_lock`): the per-thread decision lock is in memory;
- security headers on every response: CSP `default-src 'none'`, no framing, no referrer,
  no caching;
- a signed session cookie (HttpOnly, Secure, SameSite=Strict) that ends 12 hours after
  sign-in whatever the activity, and is revoked server-side on sign-out (live session
  ids are kept in memory: a restart signs everyone out); sign-in is one passphrase,
  checked against its scrypt hash, one attempt at a time, with 5 failures locking
  sign-in for 15 minutes;
- form posts are size-limited (no file parts, a handful of fields);
- a CSRF token on every POST;
- every value rendered through Jinja2 autoescaping. Previews are agent output, which
  can carry fetched web text: untrusted, shown as escaped text only.

Agents and the policy are passed in, never imported (ADR-0014). Needs the `inbox` extra.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from measured_evals import ModelPrice
from measured_ops.agent import Agent
from measured_ops.checkpoint import open_checkpointer
from measured_ops.inbox.auth import LoginThrottle, verify_passphrase
from measured_ops.inbox.service import AlreadyDecided, Inbox, NotReady, PendingItem, Refused
from measured_ops.inbox.settings import InboxSettings, single_process_lock
from measured_ops.judge_log import JudgeLog
from measured_ops.killswitch import KillSwitchEngaged
from measured_ops.policy import ApprovalPolicy
from measured_ops.supervisor import build_supervisor
from measured_ops.tools.executors import Executors

log = logging.getLogger("measured_ops.inbox")

SESSION_COOKIE = "__Host-mo_inbox"  # __Host-: Secure, Path=/, no Domain, enforced by browsers
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
DEFAULT_FEEDBACK_MAX = 4000


def feedback_max(item: PendingItem) -> int:
    """The feedback limit from the interrupt's response_schema (ADR-0016)."""
    prop = item.response_schema.get("properties", {}).get("feedback", {})
    for option in [prop, *prop.get("anyOf", [])]:
        if isinstance(option, dict) and isinstance(option.get("maxLength"), int):
            return int(option["maxLength"])
    return DEFAULT_FEEDBACK_MAX


def create_inbox_app(
    settings: InboxSettings,
    agents: Sequence[Agent],
    policy: ApprovalPolicy,
    *,
    prices: Mapping[str, ModelPrice] | None = None,
    eval_context: Mapping[str, object] | None = None,
    judge_log: JudgeLog | None = None,
    executors: Executors | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Build the inbox app. Refuses settings without usable secrets (no defaults).

    The lifespan takes the single-process lock, opens the checkpointer and compiles the
    supervisor with `agents`, `policy`, `eval_context` and `executors` (C5): the same graph the runs
    paused in. `prices` (default: the bundled demo table) prices each run's usage.
    """
    settings.check()
    session_seconds = settings.session_hours * 3600
    throttle = LoginThrottle(clock=clock)
    # One sign-in attempt at a time: the throttle check, the (slow, awaited) scrypt
    # verify and recording the failure must not interleave, or parallel guesses all
    # get checked before any failure counts. One user, so serialising costs nothing.
    signin_lock = asyncio.Lock()
    live_sessions: dict[str, float] = {}  # session id -> sign-in time; revoked on sign-out
    # Loaded once, now, and never re-read: a running inbox must not pair its code with
    # templates changed on disk later (a 500 on Hassan's phone after an upgrade).
    templates = Environment(
        loader=PackageLoader("measured_ops.inbox", "templates"),
        autoescape=select_autoescape(["html"]),
        auto_reload=False,
        cache_size=-1,
    )
    templates.filters["urlpart"] = lambda value: quote(str(value), safe="")
    templates.filters["dollars"] = lambda v: "unknown" if v is None else f"${v:,.4f}"
    for name in templates.list_templates(extensions=["html"]):
        templates.get_template(name)
    css = files("measured_ops.inbox").joinpath("templates", "inbox.css").read_text("utf-8")
    js = files("measured_ops.inbox").joinpath("templates", "inbox.js").read_text("utf-8")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with single_process_lock(settings.lock_path):
            async with open_checkpointer(settings.checkpointer_url) as saver:
                graph = build_supervisor(
                    agents, policy, eval_context=eval_context, executors=executors
                ).compile(checkpointer=saver)
                app.state.inbox = Inbox(
                    graph,
                    policy,
                    prices=prices,
                    revision_timeout_s=settings.revision_timeout_s,
                    judge_log=judge_log,
                )
                log.info("inbox: serving runs from %s", settings.checkpointer_url.split(":")[0])
                await app.state.inbox.start()  # continue revisions a restart cut off
                try:
                    yield
                finally:
                    await app.state.inbox.aclose()  # cut-off revisions continue on next start

    app = FastAPI(
        title="Measured Ops inbox",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.templates = templates

    # ---- errors: always an HTML page, never a JSON body a phone downloads -------------

    def error_page(status: int, heading: str, text: str) -> HTMLResponse:
        html = templates.get_template("error.html").render(
            heading=heading, message=text, signed_in=False
        )
        return HTMLResponse(html, status_code=status, headers=SECURITY_HEADERS)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        headings = {404: "Not found", 405: "Not allowed", 401: "Sign in", 403: "Not allowed"}
        heading = headings.get(exc.status_code, "Something's not right")
        text = (
            "That page doesn't exist."
            if exc.status_code == 404
            else "That request isn't allowed here."
        )
        return error_page(exc.status_code, heading, text)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> HTMLResponse:
        return error_page(400, "Bad request", "The form wasn't complete. Reload and try again.")

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> HTMLResponse:
        log.exception("inbox: error on %s %s", request.method, request.url.path, exc_info=exc)
        return error_page(
            500,
            "Something went wrong",
            "The inbox logged the error. If you were deciding, reload the list to see "
            "whether it went through before trying again.",
        )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=SESSION_COOKIE,
        max_age=session_seconds,  # cookie lifetime; the 12-hour cap is login_at below
        same_site="strict",
        https_only=True,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    # ---- helpers ------------------------------------------------------------------------

    def inbox(request: Request) -> Inbox:
        found: Inbox = request.app.state.inbox
        return found

    def csrf_token(request: Request) -> str:
        token = request.session.get("csrf")
        if not isinstance(token, str) or not token:
            token = secrets.token_urlsafe(32)
            request.session["csrf"] = token
        return token

    def csrf_ok(request: Request, sent: object) -> bool:
        expected = request.session.get("csrf")
        return (
            isinstance(expected, str)
            and bool(expected)
            and isinstance(sent, str)
            and hmac.compare_digest(sent, expected)
        )

    def signed_in(request: Request) -> bool:
        session = request.session
        sid = session.get("sid")
        login_at = live_sessions.get(sid) if isinstance(sid, str) else None
        if session.get("auth") is not True or login_at is None:
            return False
        if clock() - login_at >= session_seconds:
            live_sessions.pop(str(sid), None)
            session.clear()
            return False
        return True

    def start_session(request: Request) -> None:
        now = clock()
        for sid, at in list(live_sessions.items()):  # drop expired ids
            if now - at >= session_seconds:
                del live_sessions[sid]
        sid = secrets.token_urlsafe(32)
        live_sessions[sid] = now
        request.session.clear()  # new session on sign-in: no fixation
        request.session.update({"auth": True, "sid": sid, "csrf": secrets.token_urlsafe(32)})

    def render(request: Request, name: str, status: int = 200, **context: Any) -> HTMLResponse:
        context.setdefault("signed_in", signed_in(request))
        context.setdefault("csrf", csrf_token(request))
        html = templates.get_template(name).render(**context)
        return HTMLResponse(html, status_code=status)

    def to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    def message(request: Request, status: int, heading: str, text: str) -> HTMLResponse:
        return render(request, "message.html", status, heading=heading, message=text)

    def forbidden(request: Request) -> HTMLResponse:
        return message(request, 403, "Form expired", "Reload the page and try again.")

    # ---- static -------------------------------------------------------------------------

    @app.get("/static/inbox.css")
    async def stylesheet() -> Response:
        return Response(css, media_type="text/css")

    @app.get("/static/inbox.js")
    async def script() -> Response:
        # the inbox's only script, first-party (ADR-0020); pages work without it
        return Response(js, media_type="text/javascript")

    # ---- sign-in ------------------------------------------------------------------------

    # HEAD too: `curl -I .../login` is the runbook's "is it up?" check (docs/INBOX.md).
    @app.api_route("/login", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        if signed_in(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = await request.form(max_files=0, max_fields=4)
        if not csrf_ok(request, form.get("csrf")):
            return forbidden(request)
        passphrase = form.get("passphrase")
        async with signin_lock:
            if throttle.locked():
                minutes = max(1, round(throttle.retry_after() / 60))
                error = f"Too many failed attempts. Try again in about {minutes} min."
                return render(request, "login.html", 429, error=error)
            ok = isinstance(passphrase, str) and await asyncio.to_thread(
                verify_passphrase, passphrase, settings.passphrase_hash
            )
            if not ok:
                throttle.record_failure()
                log.warning("inbox: failed sign-in")
                return render(request, "login.html", 401, error="Wrong passphrase.")
            throttle.reset()
        start_session(request)
        log.info("inbox: signed in")
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        if not signed_in(request):
            return to_login()
        form = await request.form(max_files=0, max_fields=2)
        if not csrf_ok(request, form.get("csrf")):
            return forbidden(request)
        live_sessions.pop(str(request.session.get("sid")), None)  # revoke, not just forget
        request.session.clear()
        return to_login()

    # ---- the inbox ----------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def waiting(request: Request) -> Response:
        if not signed_in(request):
            return to_login()
        items = await inbox(request).list_pending()
        running = await inbox(request).in_progress()
        flash = request.session.pop("flash", None)
        return render(request, "list.html", items=items, running=running, flash=flash)

    # `:path` so a thread id containing "/" still routes (links quote it fully).
    @app.get("/a/{thread_id:path}", response_class=HTMLResponse)
    async def detail(request: Request, thread_id: str) -> Response:
        if not signed_in(request):
            return to_login()
        item = await inbox(request).get(thread_id)
        if item is None:
            return message(request, 404, "Not waiting", "This run isn't waiting for a decision.")
        return render(request, "detail.html", item=item, feedback_max=feedback_max(item))

    @app.post("/a/{thread_id:path}/decision")
    async def decision(request: Request, thread_id: str) -> Response:
        if not signed_in(request):
            return to_login()
        form = await request.form(max_files=0, max_fields=6)
        if not csrf_ok(request, form.get("csrf")):
            return forbidden(request)
        fields = {k: form.get(k) for k in ("interrupt_id", "action_id", "decision", "feedback")}
        interrupt_id, action_id, choice, feedback = (
            v if isinstance(v, str) else "" for v in fields.values()
        )
        try:
            # Explicit (ADR-0020): Revise needs text and a revision left; Reject and stop
            # drops any text, so it can never queue a revision.
            if choice == "approve":
                approved, text = True, feedback
            elif choice == "revise":
                if not feedback.strip():
                    raise Refused("write what to change, or use Reject and stop")
                current = await inbox(request).get(thread_id)
                if current is not None and current.revision >= current.max_revisions:
                    raise Refused("no revisions left: approve it, or reject and stop")
                approved, text = False, feedback
            elif choice == "reject":
                approved, text = False, ""
            else:
                raise Refused("choose Approve, Revise or Reject and stop")
            outcome = await inbox(request).decide(
                thread_id,
                interrupt_id=interrupt_id,
                action_id=action_id,
                approved=approved,
                feedback=text,
            )
        except AlreadyDecided:
            return message(
                request, 409, "Already decided", "This action was already decided. Nothing changed."
            )
        except Refused as exc:
            item = await inbox(request).get(thread_id)
            if item is None:
                return message(request, 409, "Already decided", "This run isn't waiting any more.")
            return render(
                request,
                "detail.html",
                400,
                item=item,
                feedback_max=feedback_max(item),
                error=str(exc),
            )
        verb = "Approved" if outcome.approved else "Rejected"
        if not outcome.approved and choice == "reject":
            verb = "Rejected and stopped"
        statuses = {o.status for o in outcome.executed}
        ran = (" Applied." if "applied" in statuses else "") + (
            " Already in place." if "already_applied" in statuses else ""
        )
        if outcome.failed:
            more = " Running it failed: the run is under Stopped; Retry when the tool is back."
        elif outcome.revising:
            more = f"{ran} Revising: the new draft comes back here when it's ready."
        else:
            more = ran + (" The run has another action waiting." if outcome.still_paused else "")
        request.session["flash"] = f"{verb}.{more}"
        return RedirectResponse("/", status_code=303)

    @app.get("/ready", response_class=HTMLResponse)
    async def ready(request: Request) -> Response:
        if not signed_in(request):
            return to_login()
        items = await inbox(request).list_ready()
        flash = request.session.pop("flash", None)
        return render(request, "ready.html", items=items, flash=flash)

    @app.post("/ready/{action_id}/posted")
    async def mark_posted(request: Request, action_id: str) -> Response:
        # Only a note that Hassan posted it (ADR-0020): no run is resumed or touched.
        if not signed_in(request):
            return to_login()
        form = await request.form(max_files=0, max_fields=2)
        if not csrf_ok(request, form.get("csrf")):
            return forbidden(request)
        try:
            await inbox(request).mark_posted(action_id)
        except NotReady:
            return message(request, 404, "Not found", "That draft isn't ready to paste.")
        request.session["flash"] = "Marked as posted."
        return RedirectResponse("/ready", status_code=303)

    @app.post("/r/{thread_id:path}/retry")
    async def retry(request: Request, thread_id: str) -> Response:
        if not signed_in(request):
            return to_login()
        form = await request.form(max_files=0, max_fields=2)
        if not csrf_ok(request, form.get("csrf")):
            return forbidden(request)
        try:
            await inbox(request).retry(thread_id)
        except AlreadyDecided:
            return message(request, 409, "Not stopped", "This run isn't stopped. Nothing changed.")
        except KillSwitchEngaged:
            return message(
                request,
                409,
                "Kill switch is on",
                "Nothing runs until the kill switch is released. Nothing changed.",
            )
        request.session["flash"] = "Retrying."
        return RedirectResponse("/", status_code=303)

    return app
