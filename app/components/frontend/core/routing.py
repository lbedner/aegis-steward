"""
Router for the Overseer dashboard.

Maps route strings to ``BaseView`` subclasses and drives view transitions
through the lifecycle hooks. The flow on each navigation:

1. Reentrancy guard rejects the call if a route change is already in flight.
2. If the destination is auth-protected and no session
   token exists, redirect to ``/login``.
3. Look up the ``BaseView`` class for the route in ``ROUTE_TO_VIEW``.
4. Instantiate it.
5. Call ``on_leave()`` on the currently-active view, if any. **This is the
   hook that lets dashboards cancel background tasks before being replaced.**
6. Replace ``page.views`` with the new view.
7. Call ``on_enter(params)`` with parsed query-string params.

The router doesn't manage shared state — that lives in ``SessionState``.
It just owns the route table and the lifecycle wiring.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import flet as ft

from app.components.frontend.auth.session import is_authenticated
from app.components.frontend.controls.snack_bar import ErrorSnackBar
from app.components.frontend.core.routes import LOGIN_ROUTE, PUBLIC_ROUTES
from app.core.log import logger

if TYPE_CHECKING:
    from app.components.frontend.controls.views.base import BaseView


# Route registry. Populated by ``register_route`` from the app bootstrap.
ROUTE_TO_VIEW: dict[str, type[BaseView]] = {}

# Key for the per-session reentrancy guard, stored on ``page.data``. It must
# be session-scoped: a module-level guard is shared by every session in the
# process, so two sessions routing at the same instant (a second tab, a
# reconnect) would block each other — and the blocked session renders blank
# forever, since its view is never built.
_ROUTE_IN_FLIGHT_KEY = "_route_in_flight"


def register_route(route: str, view_cls: type[BaseView]) -> None:
    """Register a route → view mapping. Called once during app bootstrap."""
    ROUTE_TO_VIEW[route] = view_cls
    logger.debug("router.route_registered", route=route, view=view_cls.__name__)


def is_authenticated_route(route: str) -> bool:
    """Return True if the route requires authentication."""
    base = urlparse(route).path.rstrip("/") or "/"
    return base not in PUBLIC_ROUTES


def redirect_to_login(page: ft.Page) -> None:
    """Send the user to the login page if they're not already there."""
    base = urlparse(page.route or "").path.rstrip("/") or "/"
    if base != LOGIN_ROUTE:
        logger.info("router.redirect_to_login", from_route=page.route, to=LOGIN_ROUTE)
        page.go(LOGIN_ROUTE)


def _extract_query_params(route: str) -> dict[str, Any]:
    parsed = urlparse(route)
    raw = parse_qs(parsed.query)
    return {k: (v[0] if len(v) == 1 else v) for k, v in raw.items()}


async def _call_on_leave(page: ft.Page) -> None:
    """Call ``on_leave`` on the topmost view, if any."""
    if not page.views:
        return
    current = page.views[-1]
    if hasattr(current, "on_leave"):
        try:
            await current.on_leave()
        except Exception:
            logger.exception(
                "router.on_leave.failed",
                view=type(current).__name__,
                session_id=page.session_id,
            )


async def route_change(page: ft.Page, event: ft.RouteChangeEvent) -> None:
    """``page.on_route_change`` handler. Drives the View lifecycle."""
    if page.data is None:
        page.data = {}
    if page.data.get(_ROUTE_IN_FLIGHT_KEY):
        logger.warning(
            "router.reentrancy_blocked",
            current=page.route,
            attempted=event.route,
            session_id=page.session_id,
        )
        return

    page.data[_ROUTE_IN_FLIGHT_KEY] = True
    try:
        raw_route = event.route or "/"
        route = deepcopy(raw_route).rstrip("/") or "/"
        logger.info("router.route_change", route=route, raw=raw_route)

        if is_authenticated_route(route) and not await is_authenticated(page):
            redirect_to_login(page)
            return

        base = urlparse(route).path.rstrip("/") or "/"
        view_cls = ROUTE_TO_VIEW.get(base)
        if view_cls is None:
            logger.error(
                "router.no_view_for_route",
                route=route,
                base=base,
                registered=list(ROUTE_TO_VIEW.keys()),
            )
            ErrorSnackBar(f"Page not found: {base}").launch(page)
            return

        params = _extract_query_params(route)

        # Lifecycle: on_leave outgoing → swap → on_enter incoming.
        await _call_on_leave(page)

        new_view = view_cls(page=page, route=base)
        page.views.clear()
        page.views.append(new_view)
        page.update()

        try:
            await new_view.on_enter(params)
        except Exception:
            logger.exception(
                "router.on_enter.failed",
                view=view_cls.__name__,
                route=route,
            )
            ErrorSnackBar("Failed to load page.").launch(page)
    except Exception:
        logger.exception("router.route_change.error", route=event.route)
        try:
            ErrorSnackBar("Navigation failed.").launch(page)
        except Exception:
            pass
    finally:
        page.data[_ROUTE_IN_FLIGHT_KEY] = False


async def view_pop(page: ft.Page, event: ft.ViewPopEvent) -> None:
    """``page.on_view_pop`` handler. Pops the top view and navigates back."""
    try:
        if len(page.views) <= 1:
            logger.debug("router.view_pop.bottom_of_stack")
            return
        await _call_on_leave(page)
        page.views.pop()
        top = page.views[-1]
        page.go(top.route or "/")
    except Exception:
        logger.exception("router.view_pop.error")
        try:
            ErrorSnackBar("Could not go back.").launch(page)
        except Exception:
            pass
