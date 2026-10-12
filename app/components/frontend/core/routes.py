"""
Route constants for the Overseer dashboard.

Single source of truth for path strings. The router (``core.routing``) maps
each route to a concrete ``BaseView`` subclass via ``ROUTE_TO_VIEW``.
"""

from __future__ import annotations

DASHBOARD_ROUTE: str = "/"
LOGIN_ROUTE: str = "/login"
REGISTER_ROUTE: str = "/register"


PUBLIC_ROUTES: tuple[str, ...] = (LOGIN_ROUTE, REGISTER_ROUTE)
"""Routes that do not require authentication."""
