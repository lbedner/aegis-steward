"""User management service."""

from collections.abc import Iterable

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import get_password_hash
from app.core.time import utcnow
from app.models.user import User, UserCreate
from app.services.auth.domains.users.base import (
    RegistrationClosedError as RegistrationClosedError,
)
from app.services.auth.domains.users.base import (
    RestoreConflictError as RestoreConflictError,
)
from app.services.auth.domains.users.base import (
    UserServiceBase,
)
from app.services.auth.domains.users.deletion import DeletionMixin
from app.services.auth.domains.users.hooks import run_post_user_created
from app.services.auth.domains.users.passwords import PasswordsMixin
from app.services.auth.domains.users.verification import VerificationMixin


class UserService(
    DeletionMixin,
    PasswordsMixin,
    VerificationMixin,
    UserServiceBase,
):
    """Service for managing users with async database operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_user(self, user_data: UserCreate) -> User:
        """Create a new user asynchronously."""
        # Hash the password
        hashed_password = get_password_hash(user_data.password)
        # Create user object.
        #
        # ``is_active`` is hardcoded True rather than read from input: the
        # narrowed ``UserCreate`` no longer exposes it, and a fresh signup
        # is by definition active. Deactivation is an admin action that
        # goes through ``deactivate_user``.
        user = User(
            email=user_data.email.lower(),
            full_name=user_data.full_name,
            hashed_password=hashed_password,
            is_active=True,
            is_verified=True,
            created_at=utcnow(),
        )

        # Save to database. Flush so ``user.id`` is populated before
        # hooks run; they get a real id (e.g. for FK rows they stage).
        self.db.add(user)
        await self.db.flush()
        await run_post_user_created(self.db, user)
        await self.db.commit()

        return user

    async def get_user_by_email(self, email: str) -> User | None:
        """Get user by email address asynchronously. Skips soft-deleted users."""
        statement = select(User).where(
            User.email == email.lower(),
            User.deleted_at.is_(None),
        )
        result = await self.db.exec(statement)
        return result.first()

    async def get_user_by_id(self, user_id: int) -> User | None:
        """Get user by ID, skipping soft-deleted rows.

        ``Session.get`` not ``select()``: the identity map answers a repeat PK
        lookup without SQL, and ``deleted_at`` is filtered here instead.
        """
        user = await self.db.get(User, user_id)
        return None if user is None or user.deleted_at is not None else user

    async def get_users_by_ids(self, user_ids: list[int]) -> dict[int, User]:
        """``{id: user}`` for the given ids in one query; soft-deleted and
        unknown ids are absent."""
        if not user_ids:
            return {}
        statement = select(User).where(
            User.id.in_(user_ids),  # type: ignore[union-attr]
            User.deleted_at.is_(None),
        )
        result = await self.db.exec(statement)
        return {user.id: user for user in result.all() if user.id is not None}

    async def get_user_ids_by_emails(self, emails: Iterable[str]) -> dict[str, int]:
        """``{email: id}`` for the given emails (any case) in one query;
        soft-deleted and unknown emails are absent."""
        wanted = {email.lower() for email in emails}
        if not wanted:
            return {}
        statement = select(User.email, User.id).where(
            User.email.in_(wanted),  # type: ignore[attr-defined]
            User.deleted_at.is_(None),
        )
        result = await self.db.exec(statement)
        return dict(result.all())  # type: ignore[arg-type]

    async def update_user(self, user_id: int, **updates) -> User | None:
        """Update user data asynchronously."""
        user = await self.get_user_by_id(user_id)
        if not user:
            return None

        for field, value in updates.items():
            if hasattr(user, field):
                setattr(user, field, value)

        user.updated_at = utcnow()
        self.db.add(user)
        await self.db.commit()

        return user

    async def deactivate_user(self, user_id: int) -> User | None:
        """Deactivate a user account asynchronously."""
        return await self.update_user(user_id, is_active=False)

    async def activate_user(self, user_id: int) -> User | None:
        """Activate a user account asynchronously."""
        return await self.update_user(user_id, is_active=True)

    async def list_users(self) -> list[User]:
        """List all users in the system asynchronously. Skips soft-deleted
        users and row 0, the finance owner placeholder, which is no person."""
        statement = (
            select(User)
            .where(User.deleted_at.is_(None), User.id != 0)
            .order_by(User.created_at.desc())
        )
        result = await self.db.exec(statement)
        return list(result.all())

    async def find_existing_emails_with_prefix(
        self, prefix: str, domain: str
    ) -> list[str]:
        """Find existing emails matching ``prefix{number}@domain``.

        Filters out soft-deleted users so callers using this to pick a
        unique email don't get blocked by a stale row.
        """
        pattern = f"{prefix}%@{domain}"
        statement = select(User.email).where(
            User.email.like(pattern),
            User.deleted_at.is_(None),
        )
        result = await self.db.exec(statement)
        return list(result.all())
