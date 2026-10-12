"""FastAPI dependencies more than one service injects.

``get_owner_user_id`` centralizes owner scoping in one place: it requires a
signed-in user and scopes them to the household (``None``) - so route
handlers stay auth-agnostic and never repeat the auth guard per endpoint. ``get_admin_actor`` does the same for
an operator's change: an admin with auth, anyone without.
"""

from dataclasses import dataclass

from fastapi import Depends

from app.models.user import User
from app.services.auth.deps import (
    get_current_active_user,
    require_admin,
)


@dataclass(frozen=True)
class Actor:
    """Who made a change: the signed-in user with the auth service; nobody
    in particular without it, since whoever reaches the app may make it."""

    id: int | None = None
    email: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        """Who to name in a record of the change."""
        return self.email or "anonymous"


def actor_of(user: User | None) -> Actor:
    """``user`` as the maker of a change."""
    if user is None:
        return Actor()
    return Actor(user.id, user.email, user.display_name)


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
