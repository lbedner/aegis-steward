"""Authentication service utilities."""

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.db import release_lock
from app.core.security import verify_token
from app.models.user import User
from app.services.auth.refresh_tokens import RefreshService
from app.services.auth.users import UserService


def _build_dev_user() -> User:
    """Build a synthetic dev user with role from settings."""
    return User(
        id=0,
        email="dev@localhost.dev",
        full_name="Development User",
        hashed_password="",
        is_active=True,
        is_verified=True,
    )


def session_id_of(token: str | None) -> str | None:
    """The session a valid access token belongs to: its ``sid`` claim."""
    payload = verify_token(token) if token else None
    sid = payload.get("sid") if payload else None
    return sid if isinstance(sid, str) else None


async def get_current_user_from_token(
    token: str | None, user_service: UserService
) -> User:
    """Get current user from JWT token."""
    if not settings.AUTH_ENABLED:
        return _build_dev_user()

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token is None:
        raise credentials_exception

    # Verify token
    payload = verify_token(token)
    if payload is None:
        raise credentials_exception

    # Get user email from token
    email = payload.get("sub")
    if not isinstance(email, str) or email is None:
        raise credentials_exception

    # Get user from database
    user = await user_service.get_user_by_email(email)
    if user is None:
        raise credentials_exception

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user"
        )

    # A signed-out session's access tokens end with it, not at their expiry.
    # A token without ``sid`` predates the claim or was minted outside a
    # sign-in (load tests) and lives out its short lifetime.
    # ponytail: one indexed lookup per request; cache it if it ever shows.
    sid = payload.get("sid")
    if isinstance(sid, str) and not await RefreshService(
        user_service.db
    ).is_live_session(sid):
        raise credentials_exception

    # Release the lookup's transaction before the handler runs. On SQLite it
    # holds the write lock from BEGIN, and the request keeps this session to
    # the end, so any other session the handler opens (a service with its
    # own) waited it out and failed "database is locked". Nothing is pending
    # yet, and the user stays loaded (expire_on_commit=False).
    await release_lock(user_service.db)

    return user
