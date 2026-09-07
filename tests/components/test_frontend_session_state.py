"""Tests for SessionState — the per-session shared resources container."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.components.frontend.state.session_state import (
    SessionState,
    clear_session_state,
    get_session_state,
    init_session_state,
)
from app.core.client import APIClient


def _make_page() -> MagicMock:
    page = MagicMock()
    page.data = None
    page.session_id = "test-session"
    return page


def _mock_api_client() -> MagicMock:
    """Mocked APIClient — avoids spinning up a real httpx connection pool.

    These tests don't exercise client behavior, just that SessionState
    stores/retrieves/clears it. Using a mock keeps them fast and leak-free.
    """
    client = MagicMock(spec=APIClient)
    client.aclose = AsyncMock()
    return client


def test_init_attaches_session_state_to_page_data() -> None:
    page = _make_page()
    api_client = _mock_api_client()

    state = init_session_state(page, api_client=api_client)

    assert isinstance(state, SessionState)
    assert page.data["session_state"] is state
    assert state.api_client is api_client


def test_init_creates_page_data_dict_when_missing() -> None:
    page = _make_page()
    page.data = None

    init_session_state(page, api_client=_mock_api_client())

    assert page.data is not None
    assert "session_state" in page.data


def test_get_returns_attached_state() -> None:
    page = _make_page()
    state = init_session_state(page, api_client=_mock_api_client())

    assert get_session_state(page) is state


def test_get_raises_when_not_initialized() -> None:
    page = _make_page()
    with pytest.raises(RuntimeError, match="not been initialized"):
        get_session_state(page)


def test_get_raises_when_page_data_missing_key() -> None:
    page = _make_page()
    page.data = {"something_else": 1}
    with pytest.raises(RuntimeError, match="not been initialized"):
        get_session_state(page)


@pytest.mark.asyncio
async def test_clear_removes_session_state() -> None:
    page = _make_page()
    init_session_state(page, api_client=_mock_api_client())

    await clear_session_state(page)

    assert "session_state" not in page.data
    with pytest.raises(RuntimeError):
        get_session_state(page)


@pytest.mark.asyncio
async def test_clear_is_idempotent_when_not_initialized() -> None:
    page = _make_page()
    # Should not raise
    await clear_session_state(page)


def test_ad_hoc_get_set() -> None:
    page = _make_page()
    state = init_session_state(page, api_client=_mock_api_client())

    assert state.get("missing") is None
    assert state.get("missing", "default") == "default"

    state.set("foo", "bar")
    assert state.get("foo") == "bar"


def test_session_state_holds_page_reference() -> None:
    page = _make_page()
    state = init_session_state(page, api_client=_mock_api_client())

    # SessionState IS the canonical home for the page reference.
    assert state.page is page
