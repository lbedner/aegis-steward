"""The app's one gate: signed out, nothing answers but the way in.

Pure ASGI, so a websocket (the chat's live relay, Flet's ``/dashboard``
socket) and a mount (Flet, the static files) pass the same check as a
route; tests/components/test_request_gate.py walks every route the app
serves, so one added later cannot skip it. Public is ``PUBLIC_PATHS`` and
``PUBLIC_PREFIXES``: sign-in, its session renewal, the static files the
sign-in page loads, bare ``/health/`` and Plaid's webhook (verified by its
own signature). Signed out, a page goes to sign-in and back, anything else
is a 401 (htmx's is renewed by auth.js) and a socket is closed.

It also refuses a cross-site write before anything else, sign-in included:
an unsafe method, or a socket, whose ``Sec-Fetch-Site`` or ``Origin`` says
another site sent it. A request with neither is not a browser's (a bearer
client, the CLI) and carries no cookie a page could have borrowed.

Registered with ``TrustedHostMiddleware`` (``ALLOWED_HOSTS``) outside it,
so a request for a host this app is not is refused first.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from app.components.backend.startup.finance_webhook_tunnel import (
    PLAID_WEBHOOK_PATH,
    tunnel_metrics_url,
)
from app.core.config import settings
from app.core.db import get_async_db
from app.core.security import SESSION_COOKIE
from app.services.auth.service import get_current_user_from_token
from app.services.auth.users import UserService

LOGIN = "/login"
OVERSEER_LOGIN = "/overseer/login"

PUBLIC_PATHS = frozenset(
    {
        LOGIN,
        OVERSEER_LOGIN,
        "/logout",
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/health",
        "/health/",
        PLAID_WEBHOOK_PATH,
    }
)
PUBLIC_PREFIXES = ("/static/",)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# The dev overlay's Plaid tunnel forwards the public hostname it was given.
TUNNEL_HOSTS = ["*.trycloudflare.com"]


def is_public(path: str) -> bool:
    """Whether ``path`` answers signed out."""
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _cross_site(conn: HTTPConnection) -> bool:
    """Whether a browser says another site sent this."""
    site = conn.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        return True
    origin = conn.headers.get("origin")
    return origin is not None and urlsplit(origin).netloc != conn.headers.get("host")


def _writes(scope: Scope) -> bool:
    return scope["type"] == "websocket" or scope["method"] not in SAFE_METHODS


async def signed_in(conn: HTTPConnection) -> bool:
    """Whether the session cookie or a bearer token names an active user
    whose session is live (``get_current_user_from_token``), read on the
    session a route would get: ``get_async_db``, or what a test put there."""
    token = conn.cookies.get(SESSION_COOKIE)
    bearer = conn.headers.get("authorization", "")
    if token is None and bearer.lower().startswith("bearer "):
        token = bearer[7:]
    if token is None and settings.AUTH_ENABLED:
        return False
    overrides = getattr(conn.scope.get("app"), "dependency_overrides", {})
    sessions = overrides.get(get_async_db, get_async_db)()
    try:
        await get_current_user_from_token(token, UserService(await anext(sessions)))
    except HTTPException:
        return False
    finally:
        await sessions.aclose()
    return True


def _turned_away(conn: HTTPConnection) -> Response:
    """A page to sign-in (back here after), anything else a 401."""
    path = conn.url.path
    page = (
        conn.scope.get("method") == "GET"
        and "text/html" in conn.headers.get("accept", "")
        and conn.headers.get("hx-request") != "true"
    )
    if not page:
        return PlainTextResponse("Not signed in", status_code=401)
    login = OVERSEER_LOGIN if path.startswith("/overseer") else LOGIN
    return RedirectResponse(f"{login}?next={quote(path)}", status_code=303)


class RequestGate:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        path = scope["path"]
        refusal: Response | WebSocketClose
        if path != PLAID_WEBHOOK_PATH and _writes(scope) and _cross_site(conn):
            refusal = PlainTextResponse("Cross-site request refused", status_code=403)
        elif is_public(path) or await signed_in(conn):
            await self.app(scope, receive, send)
            return
        else:
            refusal = _turned_away(conn)
        if scope["type"] == "websocket":
            refusal = WebSocketClose()
        await refusal(scope, receive, send)


def register_middleware(app: FastAPI) -> None:
    """The gate, with the host check outside it."""
    app.add_middleware(RequestGate)
    hosts = [*settings.ALLOWED_HOSTS, *(TUNNEL_HOSTS if tunnel_metrics_url() else [])]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
