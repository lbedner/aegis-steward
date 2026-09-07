"""Contract tests for BaseView."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.components.frontend.controls.views.base import BaseView


class _Concrete(BaseView):
    """Minimal subclass implementing the required abstract methods."""

    def __init__(self, page: Any) -> None:
        super().__init__(page=page, route="/concrete")
        self.entered_with: dict[str, Any] | None = None
        self.left = False
        self.refreshed = False

    async def on_enter(self, params: dict[str, Any]) -> None:
        self.entered_with = params

    async def on_leave(self) -> None:
        self.left = True

    async def on_refresh(self) -> None:
        self.refreshed = True


def test_cannot_instantiate_baseview_directly() -> None:
    with pytest.raises(TypeError):
        BaseView(page=MagicMock(), route="/test")  # type: ignore[abstract]


def test_subclass_missing_on_enter_cannot_instantiate() -> None:
    class MissingOnEnter(BaseView):
        async def on_refresh(self) -> None:
            return None

    with pytest.raises(TypeError):
        MissingOnEnter(page=MagicMock(), route="/test")  # type: ignore[abstract]


def test_subclass_missing_on_refresh_cannot_instantiate() -> None:
    class MissingOnRefresh(BaseView):
        async def on_enter(self, params: dict[str, Any]) -> None:
            return None

    with pytest.raises(TypeError):
        MissingOnRefresh(page=MagicMock(), route="/test")  # type: ignore[abstract]


def test_on_leave_has_default_noop() -> None:
    class NoLeave(BaseView):
        async def on_enter(self, params: dict[str, Any]) -> None:
            return None

        async def on_refresh(self) -> None:
            return None

    view = NoLeave(page=MagicMock(), route="/test")
    # Should not raise
    import asyncio

    asyncio.run(view.on_leave())


def test_concrete_view_holds_page_and_route() -> None:
    page = MagicMock()
    view = _Concrete(page=page)
    assert view.page is page
    assert view.route == "/concrete"


@pytest.mark.asyncio
async def test_lifecycle_methods_are_awaitable_and_pass_params() -> None:
    view = _Concrete(page=MagicMock())

    await view.on_enter({"q": "hello"})
    assert view.entered_with == {"q": "hello"}

    await view.on_refresh()
    assert view.refreshed is True

    await view.on_leave()
    assert view.left is True
