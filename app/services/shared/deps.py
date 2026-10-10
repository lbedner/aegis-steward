"""FastAPI dependencies more than one service injects.

``get_owner_user_id`` centralizes owner scoping in one place: it resolves to
the authenticated user's id when the auth service is present, else ``None``
(single-user / standalone) - so route handlers stay auth-agnostic and never
repeat the auth guard per endpoint. ``get_admin_actor`` does the same for
an operator's change: an admin with auth, anyone without.
"""

from dataclasses import dataclass


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


def actor_of(user: None = None) -> Actor:
    """Nobody signs in: a change is anyone's."""
    return Actor()


async def get_admin_actor() -> Actor:
    """No auth service: whoever reaches the app may make an operator's change."""
    return Actor()


async def get_owner_user_id() -> int | None:
    """No auth service - single-user, so rows are unscoped."""
    return None


# Nobody signs in, so a route that also serves anonymous callers scopes
# the same way.
get_optional_owner_user_id = get_owner_user_id
