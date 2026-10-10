"""The Overseer dashboard's bootstrap, and the Flet app that mounts it.

The screen itself lives in ``overseer/``: the header in ``chrome``, the
three views in ``body``, one refresh pass in ``refresh``, the tasks that
keep calling it in ``loops``, and the card factory in ``cards``. What is
left here is construction and wiring - which object gets handed to which
- plus the app-level route table in ``create_frontend_app``.
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial

import flet as ft

from app.components.frontend.controls.views.base import BaseView
from app.components.frontend.controls.views.legacy_dashboard import (
    LegacyDashboardView,
)
from app.components.frontend.core import events as frontend_events
from app.components.frontend.core.routes import DASHBOARD_ROUTE
from app.components.frontend.core.routing import (
    register_route,
    route_change,
    view_pop,
)
from app.components.frontend.core.session_health import SessionLoops
from app.components.frontend.dashboard.system_dashboard import SystemDashboard
from app.components.frontend.state.session_state import (
    get_session_state,
    init_session_state,
)
from app.core.client import APIClient

from .overseer.body import DashboardViews
from .overseer.chrome import build_chrome, toggle_theme
from .overseer.loops import WorkerStream, auto_refresh
from .overseer.refresh import refresh_dashboard as refresh_once
from .theme_manager import ThemeManager


async def setup_dashboard(view: BaseView) -> None:
    """
    Render the Overseer dashboard inside ``view``.

    Construction, then wiring. Everything that used to be a closure over
    this function's locals is a function or method in ``overseer/`` now,
    taking what it needs as arguments. Tasks are tracked on
    ``view._tasks`` so ``view.on_leave`` can cancel them cleanly when the
    router navigates away (e.g. on logout).
    """
    page = view.page
    theme_manager = ThemeManager(page)
    await theme_manager.initialize_themes()

    # Defined first, resolved last: the switcher and ``page.data`` both
    # need something to call before ``dashboard`` and ``api_client``
    # exist, and a closure reads its names at call time.
    async def refresh_dashboard() -> None:
        await refresh_once(
            api_client,
            dashboard,
            page,
            chrome.uptime_text,
            chrome.session_start,
        )

    chrome = build_chrome(page, on_project_change=refresh_dashboard)
    views = DashboardViews(page, view, chrome.view_toggle_button)

    # Create SystemDashboard with safe component references
    dashboard = SystemDashboard()
    dashboard.initialize_components(
        health_indicator_container=chrome.health_indicator_container,
        cards_container=views.component_cards_container,
        status_overview_panel=views.status_overview_panel,
        activity_feed=views.activity_feed,
        diagram_view=views.diagram_view,
        theme_manager=theme_manager,
        page=page,
    )
    # Bound here, not in ``build_chrome``: the connection check belongs
    # to the dashboard, which does not exist when the header is built.
    chrome.theme_button.on_click = partial(
        toggle_theme,
        theme_manager,
        chrome.theme_button,
        page,
        dashboard._is_page_connected,
    )

    view.controls = [chrome.header, ft.SelectionArea(content=views.body)]
    view.update()

    # Single HTTP client for the whole session. Pulled from SessionState so
    # the bearer token + 401 handler are wired automatically — no raw
    # httpx.AsyncClient anywhere in the dashboard.
    api_client = get_session_state(page).api_client

    # Register refresh function on page.data for access by any component
    if page.data is None:
        page.data = {}
    page.data["refresh_dashboard"] = refresh_dashboard
    # A modal holding a fresh reading applies it to one card rather than
    # asking for the whole board back.
    page.data["update_component"] = dashboard.update_component

    # One object owns the rules every loop shares: how long a disconnect
    # is tolerated, which exceptions are fatal, and the fact that a fatal
    # one ends ALL of this page's loops rather than the one that noticed.
    loops = SessionLoops(page, dashboard._is_page_connected)

    # Initial load and start refresh
    await refresh_dashboard()

    # The worker feed is created here but not started: the worker modal
    # starts it on show and stops it on hide, and ``view.on_leave`` stops it.
    page.data["worker_stream"] = WorkerStream(loops, page, api_client)

    # Track tasks on the view so ``view.on_leave`` can cancel them when
    # the router navigates away (e.g. on logout).
    view._tasks.append(asyncio.create_task(auto_refresh(loops, refresh_dashboard)))


def create_frontend_app() -> Callable[[ft.Page], Awaitable[None]]:
    """
    Return the slim Flet target function.

    The actual dashboard rendering lives in ``setup_dashboard`` (called by
    ``LegacyDashboardView.on_enter`` via the router). Here we just wire
    up app-level concerns: APIClient, SessionState, page-event handlers,
    the route table, and the initial navigation.
    """

    async def flet_main(page: ft.Page) -> None:
        page.title = "Aegis Stack - System Dashboard"

        # APIClient: the single canonical HTTP client for the frontend.
        # The HttpOnly ``aegis_session`` cookie rides on the underlying
        # httpx cookie jar — there is no manual token plumbing in Python.
        # A 401 from any call routes the user back to /login.
        api_client = APIClient()

        # SessionState: per-session shared resources. Owns ``page``.
        init_session_state(page, api_client=api_client)

        # Register routes. Order doesn't matter; route_change picks the
        # right view by string match.
        register_route(DASHBOARD_ROUTE, LegacyDashboardView)

        # Wire page event handlers. ``async`` Flet handlers are dispatched
        # via ``page.run_task`` under the hood.
        async def _on_route_change(event: ft.RouteChangeEvent) -> None:
            await route_change(page, event)

        async def _on_view_pop(event: ft.ViewPopEvent) -> None:
            await view_pop(page, event)

        page.on_route_change = _on_route_change
        page.on_view_pop = _on_view_pop
        page.on_connect = frontend_events.on_connect
        page.on_disconnect = frontend_events.on_disconnect
        page.on_error = frontend_events.on_error
        page.on_resize = frontend_events.on_resize

        # Initial navigation. Router's auth guard handles redirects.
        initial = DASHBOARD_ROUTE
        page.go(initial)

    return flet_main
