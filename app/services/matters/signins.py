"""Sign-ins: how you get into someone else's account.

The secret is encrypted with the row's own context, so ciphertext moved
from one row to another fails to decrypt rather than handing back
somebody else's password. Reading it back is a deliberate call - the
list never carries it - because a page that renders every password to
draw a list has put them in a log, a cache and a screenshot.

What this protects: a stolen database file. What it does not: a stolen
machine, because the running app holds the key. Saying which is which
is the difference between a safe and a drawer with a lock drawn on it.

No agent tool reads this module, and none should. Every other matter
surface is registered for Illiana to work from; a password is the one
thing a model has no business being handed, and the absence is
deliberate rather than an oversight to be corrected later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import decrypt_secret, encrypt_secret
from app.services.matters.facts import web_address
from app.services.matters.models import SignIn


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _context(sign_in_id: int) -> str:
    """AAD binding the ciphertext to its row."""
    return f"sign_in:{sign_in_id}:secret"


class SignInService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(
        self,
        *,
        party_id: int,
        label: str,
        site_party_id: int | None = None,
        url: str | None = None,
        username: str | None = None,
        secret: str | None = None,
        note: str | None = None,
        owner_user_id: int | None = None,
    ) -> SignIn:
        named = " ".join((label or "").split())
        if not named:
            raise ValueError("Give the sign-in a name.")
        sign_in = SignIn(
            owner_user_id=owner_user_id,
            party_id=party_id,
            site_party_id=site_party_id,
            label=named,
            url=web_address(url),
            username=(username or "").strip() or None,
            note=(note or "").strip() or None,
        )
        self.db.add(sign_in)
        # The row's id is the binding, so the secret is written on a
        # second pass rather than guessed at before there is one.
        await self.db.flush()
        if secret:
            sign_in.secret_encrypted = encrypt_secret(
                secret, context=_context(sign_in.id)
            )
            self.db.add(sign_in)
            await self.db.flush()
        return sign_in

    async def get(self, sign_in_id: int) -> SignIn | None:
        found = await self.db.get(SignIn, sign_in_id)
        return found if found and found.deleted_at is None else None

    async def for_party(self, party_id: int) -> list[SignIn]:
        return list(
            (
                await self.db.exec(
                    select(SignIn)
                    .where(SignIn.party_id == party_id)
                    .where(col(SignIn.deleted_at).is_(None))
                    .order_by(col(SignIn.label))
                )
            ).all()
        )

    async def change(
        self, sign_in_id: int, changes: dict[str, Any], secret: str | None = None
    ) -> SignIn | None:
        """Edit the details, and the secret only when one is given.

        A blank password box means "leave it alone", never "clear it":
        an edit to the username is not a statement about the password,
        and the form cannot show the old one to put it back.
        """
        sign_in = await self.get(sign_in_id)
        if sign_in is None:
            return None
        if "label" in changes:
            named = " ".join(str(changes["label"] or "").split())
            if not named:
                raise ValueError("Give the sign-in a name.")
            sign_in.label = named
        if "site_party_id" in changes:
            site = changes["site_party_id"]
            sign_in.site_party_id = int(site) if site else None
        if "url" in changes:
            sign_in.url = web_address(str(changes["url"] or ""))
        if "username" in changes:
            sign_in.username = str(changes["username"] or "").strip() or None
        if "note" in changes:
            sign_in.note = str(changes["note"] or "").strip() or None
        if secret:
            sign_in.secret_encrypted = encrypt_secret(
                secret, context=_context(sign_in_id)
            )
        sign_in.updated_at = _utcnow()
        self.db.add(sign_in)
        await self.db.flush()
        return sign_in

    async def reveal(self, sign_in_id: int) -> str | None:
        """The password itself, asked for on purpose.

        Never part of a listing. A key that has been rotated leaves the
        ciphertext unreadable, which reads as "no password stored" if it
        is swallowed - so this lets the failure out.
        """
        sign_in = await self.get(sign_in_id)
        if sign_in is None or not sign_in.secret_encrypted:
            return None
        return decrypt_secret(sign_in.secret_encrypted, context=_context(sign_in_id))

    async def forget(self, sign_in_id: int) -> bool:
        sign_in = await self.get(sign_in_id)
        if sign_in is None:
            return False
        sign_in.deleted_at = _utcnow()
        self.db.add(sign_in)
        await self.db.flush()
        return True


def drawn(
    sign_in: SignIn, places: dict[int, dict[str, str]] | None = None
) -> dict[str, Any]:
    """One sign-in as a page lists it - which is never the password."""
    place = (places or {}).get(sign_in.site_party_id or -1, {})
    return {
        "id": sign_in.id,
        "party_id": sign_in.party_id,
        "site_party_id": sign_in.site_party_id,
        "site": place.get("name", ""),
        # The exact page if one was given, otherwise where the place
        # lives. One row, one way in.
        "url": sign_in.url or place.get("website", ""),
        "label": sign_in.label,
        "username": sign_in.username or "",
        "has_secret": bool(sign_in.secret_encrypted),
        "note": sign_in.note or "",
    }
