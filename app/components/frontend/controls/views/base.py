"""
Base class for routed Overseer views.

Subclasses extend ``ft.View`` directly via this ABC. They compose their
layout inline in ``__init__`` (no ``_build_*`` helpers — the body of
``__init__`` IS the layout) and assign to ``self.controls``.

Three lifecycle hooks tie views into the router and the page event system:

- ``on_enter(params)``: called by the router after the view is appended to
  ``page.views``. Load data, spawn tasks, subscribe to streams here.
- ``on_leave()``: called by the router before the view is replaced or
  popped. Cancel tasks, close streams, release resources here. Always
  paired with ``on_enter`` — this is what makes view transitions clean.
- ``on_refresh()``: called by ``on_connect`` when the user refreshes the
  browser (a new WebSocket connects to an existing route). Reload data
  without re-mounting the view.

``on_enter`` and ``on_refresh`` are abstract because every view should
explicitly think about both. ``on_leave`` defaults to a no-op because
many views genuinely have nothing to clean up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import flet as ft


class BaseView(ft.View, ABC):
    """ABC for routed views in the Overseer."""

    def __init__(
        self,
        *,
        page: ft.Page,
        route: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(route=route, **kwargs)
        # ft.Control.page has a setter; assigning here gives subclasses a
        # stable handle they can use during ``__init__`` (before Flet has
        # attached the view to the page).
        self.page = page

    @abstractmethod
    async def on_enter(self, params: dict[str, Any]) -> None:
        """Load initial state once the view is on the page."""

    async def on_leave(self) -> None:
        """Cancel tasks / close streams. Default no-op."""
        return None

    @abstractmethod
    async def on_refresh(self) -> None:
        """Reload data without remounting (browser refresh path)."""
