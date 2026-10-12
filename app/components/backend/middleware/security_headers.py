"""The headers every response carries.

``nosniff``: a response is the type it says. ``SAMEORIGIN`` rather than
``DENY``: the document dialog embeds a PDF in an ``<object>``, which
X-Frame-Options governs like a frame, and only this origin may do it.
``same-origin`` referrers: a link out does not hand another site the path
it came from. A route that sets one of these itself keeps its own.

Every HTML page also gets the script policy (#492): scripts from this
origin (third-party ones are vendored, static/vendor), and inline ones carrying this
request's nonce (``csp_nonce()``, a Jinja global). A swapped fragment's
inline script carries ITS response's nonce, not the page's, so it is
blocked: behaviour a fragment needs goes in static JS or Alpine.
"""

from __future__ import annotations

from contextvars import ContextVar
import secrets

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}

# ponytail: 'unsafe-eval' because Alpine's standard build compiles every
# x-data/@click with new Function (and htmx's trigger filters, change[...],
# eval too), so injected markup carrying Alpine attributes still runs.
# Ceiling: Alpine's CSP build (rewrites the chat's inline Alpine), then a
# nonce-only script-src.
POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' 'nonce-{nonce}' 'unsafe-eval'",
        # style="" attributes in the templates; htmx's indicator <style>.
        "style-src 'self' 'unsafe-inline'",
        # data: CSS icons, blob: chat attachment previews, Google's favicon
        # service and its gstatic redirect (core/brand_icons.favicon_url).
        "img-src 'self' data: blob: https://www.google.com https://*.gstatic.com",
        # fetch, SSE and the voice relay's WebSocket ('self' covers ws/wss
        # here); a live call's SDP goes through our server, and WebRTC media
        # is not CSP's to govern.
        "connect-src 'self'",
        "media-src 'self'",
        "object-src 'self'",  # the PDF <object>
        "frame-ancestors 'self'",
        "base-uri 'self'",
        "form-action 'self'",
    )
)
# Pages that are not ours to police: Flet boots from inline script and
# wasm (and paste_capture splices in more); FastAPI's API docs inline
# theirs and load from a CDN. ponytail: unpoliced; give each its own
# policy if they ever face the internet.
UNPOLICED = ("/dashboard", "/docs", "/redoc")

_NONCE: ContextVar[str] = ContextVar("csp_nonce", default="")


def csp_nonce() -> str:
    """This request's script nonce, for ``<script nonce="{{ csp_nonce() }}">``.
    A context variable rather than request state, so a macro imported
    without context reads the same one as the page."""
    return _NONCE.get()


def _policed(path: str) -> bool:
    return not any(path == p or path.startswith(p + "/") for p in UNPOLICED)


class SecurityHeadersMiddleware:
    """Pure ASGI, so a streamed response streams through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)
        policed = _policed(scope["path"])

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
                if policed and headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault(
                        "Content-Security-Policy", POLICY.format(nonce=nonce)
                    )
            await send(message)

        token = _NONCE.set(nonce)
        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            _NONCE.reset(token)


def register_middleware(app: FastAPI) -> None:
    """Auto-discovered middleware registration."""
    app.add_middleware(SecurityHeadersMiddleware)
