"""The inbox web app (C4, ADR-0017): FastAPI, server-rendered, phone first, no JavaScript.

    GET|HEAD /login, POST /login, POST /logout
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
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware

from measured_ops.agent import Agent
from measured_ops.checkpoint import open_checkpointer
from measured_ops.inbox.auth import LoginThrottle, verify_passphrase
from measured_ops.inbox.service import AlreadyDecided, Inbox, PendingItem, Refused
from measured_ops.inbox.settings import InboxSettings, single_process_lock
from measured_ops.policy import ApprovalPolicy
from measured_ops.supervisor import build_supervisor

log = logging.getLogger("measured_ops.inbox")

SESSION_COOKIE = "__Host-mo_inbox"  # __Host-: Secure, Path=/, no Domain, enforced by browsers
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; form-action 'self'; "
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
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Build the inbox app. Refuses settings without usable secrets (no defaults).

    The lifespan takes the single-process lock, opens the checkpointer and compiles the
    supervisor with `agents` and `policy`: the same graph the runs paused in.
    """
    settings.check()
    session_seconds = settings.session_hours * 3600
    throttle = LoginThrottle(clock=clock)
    # One sign-in attempt at a time: the throttle check, the (slow, awaited) scrypt
    # verify and recording the failure must not interleave, or parallel guesses all
    # get checked before any failure counts. One user, so serialising costs nothing.
    signin_lock = asyncio.Lock()
    live_sessions: dict[str, float] = {}  # session id -> sign-in time; revoked on sign-out
    templates = Environment(
        loader=PackageLoader("measured_ops.inbox", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    templates.filters["urlpart"] = lambda value: quote(str(value), safe="")
    css = files("measured_ops.inbox").joinpath("templates", "inbox.css").read_text("utf-8")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with single_process_lock(settings.lock_path):
            async with open_checkpointer(settings.checkpointer_url) as saver:
                graph = build_supervisor(agents, policy).compile(checkpointer=saver)
                app.state.inbox = Inbox(graph, policy)
                log.info("inbox: serving runs from %s", settings.checkpointer_url.split(":")[0])
                yield

    app = FastAPI(
        title="Measured Ops inbox",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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
        flash = request.session.pop("flash", None)
        return render(request, "list.html", items=items, flash=flash)

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
            if choice not in ("approve", "reject"):
                raise Refused("choose Approve or Reject")
            outcome = await inbox(request).decide(
                thread_id,
                interrupt_id=interrupt_id,
                action_id=action_id,
                approved=choice == "approve",
                feedback=feedback,
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
        more = " The run has another action waiting." if outcome.still_paused else ""
        request.session["flash"] = f"{verb}.{more}"
        return RedirectResponse("/", status_code=303)

    return app
