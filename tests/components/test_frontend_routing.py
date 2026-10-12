"""Tests for the router.

These tests exercise the router's auth gate, redirect-to-login, and
view-lifecycle wiring — all behavior that only matters when
``include_auth=true``. Without auth, the router still ships but its auth
guard is a no-op, so this whole file is gated to keep the generated
project's test suite tidy.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from app.components.frontend.controls.views.base import BaseView
from app.components.frontend.core import routing
from app.components.frontend.core.routes import DASHBOARD_ROUTE, LOGIN_ROUTE


class _FakeView(BaseView):
    """Test double — records lifecycle calls."""

    on_enter_calls: list[dict[str, Any]] = []
    on_leave_calls: int = 0

    def __init__(self, *, page: ft.Page, route: str) -> None:
        super().__init__(page=page, route=route)
        self.entered_with: dict[str, Any] | None = None
        self.left = False

    async def on_enter(self, params: dict[str, Any]) -> None:
        self.entered_with = params
        type(self).on_enter_calls.append(params)

    async def on_leave(self) -> None:
        self.left = True
        type(self).on_leave_calls += 1

    async def on_refresh(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_router_state() -> None:
    routing.ROUTE_TO_VIEW.clear()
    _FakeView.on_enter_calls = []
    _FakeView.on_leave_calls = 0


def _make_page() -> MagicMock:
    page = MagicMock(spec=ft.Page)
    page.session_id = "test"
    page.route = "/"
    page.views = []
    # The reentrancy guard is session-scoped and lives on page.data.
    page.data = {}
    return page


def test_register_route_populates_table() -> None:
    routing.register_route("/foo", _FakeView)
    assert routing.ROUTE_TO_VIEW["/foo"] is _FakeView


def test_is_authenticated_route_excludes_public_routes() -> None:
    assert routing.is_authenticated_route(LOGIN_ROUTE) is False
    assert routing.is_authenticated_route("/dashboard") is True


def test_is_authenticated_route_strips_trailing_slash() -> None:
    assert routing.is_authenticated_route(LOGIN_ROUTE + "/") is False


@pytest.mark.asyncio
async def test_route_change_instantiates_view_and_calls_on_enter() -> None:
    routing.register_route("/test", _FakeView)
    page = _make_page()
    with patch(
        "app.components.frontend.core.routing.is_authenticated",
        AsyncMock(return_value=True),
    ):
        await routing.route_change(page, ft.RouteChangeEvent(route="/test"))

    assert len(page.views) == 1
    assert isinstance(page.views[0], _FakeView)
    assert _FakeView.on_enter_calls == [{}]


@pytest.mark.asyncio
async def test_route_change_passes_query_params() -> None:
    routing.register_route("/search", _FakeView)
    page = _make_page()
    with patch(
        "app.components.frontend.core.routing.is_authenticated",
        AsyncMock(return_value=True),
    ):
        await routing.route_change(page, ft.RouteChangeEvent(route="/search?q=hello"))

    assert _FakeView.on_enter_calls == [{"q": "hello"}]


@pytest.mark.asyncio
async def test_route_change_calls_on_leave_on_outgoing_view() -> None:
    routing.register_route("/a", _FakeView)
    routing.register_route("/b", _FakeView)
    page = _make_page()
    auth_patch = patch(
        "app.components.frontend.core.routing.is_authenticated",
        AsyncMock(return_value=True),
    )
    with auth_patch:
        await routing.route_change(page, ft.RouteChangeEvent(route="/a"))
        first = page.views[-1]
        await routing.route_change(page, ft.RouteChangeEvent(route="/b"))

    assert first.left is True
    assert _FakeView.on_leave_calls == 1


@pytest.mark.asyncio
async def test_route_change_unknown_route_does_not_swap_views() -> None:
    page = _make_page()
    page.views = [_FakeView(page=page, route=DASHBOARD_ROUTE)]
    starting_view = page.views[0]
    with patch(
        "app.components.frontend.core.routing.is_authenticated",
        AsyncMock(return_value=True),
    ):
        await routing.route_change(page, ft.RouteChangeEvent(route="/missing"))

    # View stack untouched on unknown route
    assert page.views[-1] is starting_view


@pytest.mark.asyncio
async def test_protected_route_without_auth_redirects_to_login() -> None:
    routing.register_route(DASHBOARD_ROUTE, _FakeView)
    page = _make_page()

    with patch(
        "app.components.frontend.core.routing.is_authenticated",
        AsyncMock(return_value=False),
    ):
        await routing.route_change(page, ft.RouteChangeEvent(route=DASHBOARD_ROUTE))

    page.go.assert_called_once_with(LOGIN_ROUTE)
    assert _FakeView.on_enter_calls == []


@pytest.mark.asyncio
async def test_login_route_does_not_require_auth() -> None:
    routing.register_route(LOGIN_ROUTE, _FakeView)
    page = _make_page()

    with patch(
        "app.components.frontend.core.routing.is_authenticated",
        AsyncMock(return_value=False),
    ):
        await routing.route_change(page, ft.RouteChangeEvent(route=LOGIN_ROUTE))

    assert isinstance(page.views[0], _FakeView)
    assert _FakeView.on_enter_calls == [{}]


def test_redirect_to_login_calls_page_go() -> None:
    page = _make_page()
    page.route = "/dashboard"
    routing.redirect_to_login(page)
    page.go.assert_called_once_with(LOGIN_ROUTE)


def test_redirect_to_login_no_op_when_already_on_login() -> None:
    page = _make_page()
    page.route = LOGIN_ROUTE
    routing.redirect_to_login(page)
    page.go.assert_not_called()


@pytest.mark.asyncio
async def test_reentrancy_guard_blocks_concurrent_route_changes() -> None:
    routing.register_route("/x", _FakeView)
    page = _make_page()
    page.data[routing._ROUTE_IN_FLIGHT_KEY] = True

    await routing.route_change(page, ft.RouteChangeEvent(route="/x"))

    assert _FakeView.on_enter_calls == []  # blocked


@pytest.mark.asyncio
async def test_route_guard_is_session_scoped() -> None:
    """One session's in-flight route change must not block another's - a
    process-wide guard used to leave the second browser session permanently
    blank (its view was never built)."""
    routing.register_route("/x", _FakeView)
    busy = _make_page()
    busy.data[routing._ROUTE_IN_FLIGHT_KEY] = True
    other = _make_page()

    with patch.object(routing, "is_authenticated", AsyncMock(return_value=True)):
        await routing.route_change(other, ft.RouteChangeEvent(route="/x"))

    assert len(_FakeView.on_enter_calls) == 1  # not blocked by the busy session
