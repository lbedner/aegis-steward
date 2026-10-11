"""The headers every response carries.

``nosniff``: a response is the type it says. ``SAMEORIGIN`` rather than
``DENY``: the document dialog embeds a PDF in an ``<object>``, which
X-Frame-Options governs like a frame, and only this origin may do it.
``same-origin`` referrers: a link out does not hand another site the path
it came from. A route that sets one of these itself keeps its own.

No script CSP here: the htmx pages run inline Alpine, and a policy they
pass is its own piece of work.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}


class SecurityHeadersMiddleware:
    """Pure ASGI, so a streamed response streams through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def register_middleware(app: FastAPI) -> None:
    """Auto-discovered middleware registration."""
    app.add_middleware(SecurityHeadersMiddleware)
