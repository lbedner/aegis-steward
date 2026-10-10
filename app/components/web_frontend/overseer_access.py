"""Who may use Overseer, and what its routes read with: the one place that
knows whether the stack has auth or a database.

With the auth service, admins only (``is_admin``), on every page, stream
and action. Applied once, to the pages router, so a page or partial added
later cannot forget it; tests/web/test_overseer_admin_gate.py walks them
all as a signed-in non-admin. Signed out, a page goes to the login and
anything else (a partial, a stream) is 401. Signed in but not an admin, a
page renders the refusal, which says how to get in, and anything else is a
plain 403. The user lookup is the request's own (``get_optional_user``),
shared with the route's, so it costs no query; the session it opens closes
when the handler returns, before a stream's body is sent (FastAPI 0.116,
pinned in pyproject), so a stream holds no connection.

Without auth, Overseer is open: whoever reaches the app may use it, its
writes included, like any route a stack serves without auth.
"""

from typing import Any

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.services.shared.deps import Actor, actor_of

# A page's database session: the request's own (``get_async_db``), so a
# section reading on it never waits on a second one's lock.
Db = AsyncSession
overseer_db = get_async_db

Viewer = None


async def viewer() -> None:
    """Nobody signs in."""
    return None


async def overseer_gate() -> None:
    """No auth service: everyone may use Overseer."""
    return None


async def overseer_socket_gate() -> None:
    """No auth service: a live call's socket is open, as every route is."""
    return None


def session_id(request: Request) -> None:
    """No sign-in, so no session."""
    return None


def session_expires(request: Request) -> None:
    """No sign-in, so no session cookie."""
    return None


def add_refusal_page(app: FastAPI) -> None:
    """No auth service: nobody is refused."""


async def user_names(db: Any, ids: list[str]) -> dict[str, str]:
    """No auth service, so no users to name."""
    return {}


async def overseer_actor(user: Viewer = Depends(viewer)) -> Actor:
    """Who makes an Overseer change (a secret set, a post written): the
    page-side twin of the API's ``get_admin_actor``."""
    return actor_of(user)
