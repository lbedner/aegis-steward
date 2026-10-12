"""Removing a user: soft, hard, and putting one back."""

from sqlmodel import delete, select, update

from app.core.time import utcnow
from app.models.refresh_token import RefreshToken
from app.models.user import EmailVerificationToken, PasswordResetToken, User
from app.services.auth.user_service_base import (
    RestoreConflictError,
    UserServiceBase,
)


class DeletionMixin(UserServiceBase):
    async def delete_user(self, user_id: int) -> bool:
        """Soft-delete a user by setting ``deleted_at``.

        The row stays in the database but is invisible to ``get_user_*``
        and ``list_users``. Side-effects intended to neutralize the
        user's live footprint:

        - Revokes every live refresh-token row for the user so any
          long-lived session is severed. On restore the user has to
          sign in again.

        Use ``hard_delete_user`` for permanent removal.
        """
        user = await self.get_user_by_id(user_id)
        if not user:
            return False

        now = utcnow()
        user.deleted_at = now
        self.db.add(user)

        # Revoke every live refresh token for this user. Bulk UPDATE so
        # we don't have to load N rows; mirrors ``revoke_family``.
        await self.db.exec(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

        await self.db.commit()
        return True

    async def hard_delete_user(self, user_id: int) -> bool:
        """Permanently delete a user and every row that FKs to them.

        Skips the ``deleted_at`` filter so already-soft-deleted users can
        also be purged. Tests pass without this cleanup because
        ``SQLModel.metadata.create_all`` doesn't enforce FK constraints
        the way Postgres does — production would raise an integrity
        error otherwise.

        Cleaned tables (all FK ``user.id`` without ``ON DELETE CASCADE``):

        - ``refresh_token``
        - ``password_reset_token``
        - ``email_verification_token``
        """
        statement = select(User).where(User.id == user_id)
        result = await self.db.exec(statement)
        user = result.first()
        if not user:
            return False

        await self.db.exec(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await self.db.exec(
            delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
        )
        await self.db.exec(
            delete(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user_id
            )
        )

        await self.db.delete(user)
        await self.db.commit()
        return True

    async def restore_user(self, user_id: int) -> User | None:
        """Clear ``deleted_at`` on a previously soft-deleted user.

        Looks the row up directly (bypassing the soft-delete filter on
        ``get_user_by_id``). Returns ``None`` if the row doesn't exist or
        is already live. Raises ``RestoreConflictError`` if another live user
        has claimed the email since the soft delete (partial unique
        index ``ix_user_email`` would reject the commit otherwise).
        """
        statement = select(User).where(User.id == user_id)
        result = await self.db.exec(statement)
        user = result.first()
        if not user or user.deleted_at is None:
            return None

        # Conflict check: did re-registration with the same email
        # happen while this user was soft-deleted?
        conflict_stmt = select(User).where(
            User.email == user.email,
            User.deleted_at.is_(None),
            User.id != user.id,
        )
        conflict = (await self.db.exec(conflict_stmt)).first()
        if conflict is not None:
            raise RestoreConflictError(
                f"Email '{user.email}' is already in use by an active account"
            )

        user.deleted_at = None
        user.updated_at = utcnow()
        self.db.add(user)
        await self.db.commit()
        return user
