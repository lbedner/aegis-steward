"""FastAPI dependencies more than one service injects.

``get_owner_user_id`` centralizes owner scoping in one place: it requires a
signed-in user and scopes them to the household (``None``) - so route
handlers stay auth-agnostic and never repeat the auth guard per endpoint.
``get_admin_actor`` does the same for an operator's change (a restart, a
secret set, a deploy): an admin with auth; without it, only where
``operator_refusal`` allows. Every write action depends on it (or on
Overseer's page-side twin, ``overseer_actor``), and a UI hides its write
controls where ``operator_refusal`` says why not.
"""

from dataclasses import dataclass

from fastapi import Depends, Request

from app.core.config import settings
from app.core.db import get_async_session
from app.core.tools import current_admin, current_owner_user_id
from app.models.user import User
from app.services.auth.deps import (
    get_current_active_user,
    is_admin,
    require_admin,
)

NO_AUTH_REFUSAL = (
    "Read only: with no auth service, operator changes are made only on the "
    "local dev build, at localhost. Add auth (aegis add auth) to make them "
    "here as an admin."
)


@dataclass(frozen=True)
class Actor:
    """Who made a change: the signed-in user with the auth service; nobody
    in particular without it (the local dev build's own operator)."""

    id: int | None = None
    email: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        """Who to name in a record of the change."""
        return self.email or "anonymous"


def operator_refusal(request: Request) -> str | None:
    """Why ``request`` may not make an operator's change, None when it may
    (``refusal_at`` the address it was sent to, from its page's
    ``Origin``). A page hides its write controls with this line; the
    write itself is refused by ``get_admin_actor``."""
    return refusal_at(str(request.url), request.headers.get("origin"))


def actor_of(user: User | None) -> Actor:
    """``user`` as the maker of a change."""
    if user is None:
        return Actor()
    return Actor(user.id, user.email, user.display_name)


def refusal_at(url: str, origin: str | None = None) -> str | None:
    """With auth, an operator's change is an admin's wherever they are
    (``require_admin``): nothing is refused here."""
    return None


async def get_admin_actor(admin: User = Depends(require_admin)) -> Actor:
    """An operator's change: admins only (``require_admin``)."""
    return actor_of(admin)


async def get_owner_user_id(
    _owner: User = Depends(get_current_active_user),
) -> int | None:
    """Owner scope: the household's, for whoever is signed in.

    Steward has one owner and no open signup, and every row it kept before
    sign-in carries no owner (NULL ``owner_user_id``, ``0`` where the column
    is NOT NULL, chat and memory under ``"0"``). So the signed-in owner
    scopes to ``None``, exactly what everyone saw before: sign-in decides
    who may read, never which rows, and no row is migrated.
    """
    return None


# The app's gate (``middleware/request_gate``) admits nobody signed out, so
# no route serves an anonymous caller: one scope either way.
get_optional_owner_user_id = get_owner_user_id


async def caller_is_admin() -> bool:
    """Whether the tool call being run acts for an admin: what its runner
    bound (``current_admin``: MCP's token, the CLI's shell, an admin-only
    socket), else its owner by ``is_admin``. Where sign-in is off,
    everyone is one; nobody signed in is not."""
    bound = current_admin.get()
    if bound is not None:
        return bound
    owner = current_owner_user_id.get()
    if not settings.AUTH_ENABLED or owner is None:
        return not settings.AUTH_ENABLED
    async with get_async_session() as session:
        user = await session.get(User, owner)
    return user is not None and is_admin(user)
