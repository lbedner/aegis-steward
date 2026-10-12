"""Tests for the cookie-backed frontend auth session helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.components.frontend.auth.session import is_authenticated, sign_out
from app.components.frontend.core.routes import LOGIN_ROUTE


def _make_page_with_state() -> tuple[MagicMock, MagicMock]:
    """Build a fake page wired to a session state with a mock APIClient."""
    api = MagicMock()
    api.get = AsyncMock()
    api.post = AsyncMock()
    api.clear_cookies = MagicMock()

    state = MagicMock()
    state.api_client = api
    state.current_user = None

    page = MagicMock()
    page.session_id = "sess-1"
    page.go = MagicMock()
    page.data = {"session_state": state}
    return page, state


@pytest.fixture(autouse=True)
def _patch_session_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_session_state`` reads ``page.data['session_state']``."""
    from app.components.frontend.state import session_state as ss

    def _get(page):  # type: ignore[no-untyped-def]
        return page.data["session_state"]

    monkeypatch.setattr(ss, "get_session_state", _get)
    # Patch the imported reference inside the auth.session module too.
    import app.components.frontend.auth.session as auth_session

    monkeypatch.setattr(auth_session, "get_session_state", _get)


@pytest.mark.asyncio
async def test_is_authenticated_true_on_200_caches_user() -> None:
    page, state = _make_page_with_state()
    state.api_client.get.return_value = {"id": 1, "email": "u@x.com"}

    result = await is_authenticated(page)

    assert result is True
    state.api_client.get.assert_awaited_once_with("/api/v1/auth/me")
    assert state.current_user == {"id": 1, "email": "u@x.com"}


@pytest.mark.asyncio
async def test_is_authenticated_false_when_get_returns_none() -> None:
    page, state = _make_page_with_state()
    state.api_client.get.return_value = None

    result = await is_authenticated(page)

    assert result is False
    assert state.current_user is None


@pytest.mark.asyncio
async def test_is_authenticated_false_on_empty_payload() -> None:
    page, state = _make_page_with_state()
    state.api_client.get.return_value = {}

    result = await is_authenticated(page)

    assert result is False


@pytest.mark.asyncio
async def test_sign_out_calls_logout_clears_jar_and_redirects() -> None:
    page, state = _make_page_with_state()
    state.current_user = {"id": 1}
    state.api_client.post.return_value = {"detail": "Logged out"}

    await sign_out(page)

    state.api_client.post.assert_awaited_once_with("/api/v1/auth/logout")
    state.api_client.clear_cookies.assert_called_once()
    assert state.current_user is None
    page.go.assert_called_once_with(LOGIN_ROUTE)


@pytest.mark.asyncio
async def test_sign_out_clears_jar_even_if_server_call_fails() -> None:
    """A failed /auth/logout must still drop the local jar — defense in depth."""
    page, state = _make_page_with_state()
    state.api_client.post.side_effect = RuntimeError("boom")

    await sign_out(page)

    state.api_client.clear_cookies.assert_called_once()
    page.go.assert_called_once_with(LOGIN_ROUTE)
