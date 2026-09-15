"""Reading and writing parties.

Thin on purpose. A party is a name and a way to reach it; the
interesting rules in this milestone are about what POINTS at one, and
those live with the pointers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.models import PARTY_KINDS, Party, sort_name_for


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PartyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        name: str,
        kind: str,
        owner_user_id: int | None = None,
        sort_name: str | None = None,
        contact: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> Party:
        """A new party.

        Never deduplicated by name: identity is the row. Two "Bedner"
        parties are two people until somebody says otherwise, and a
        merge is a decision rather than something that happens to them.
        """
        named = " ".join((name or "").split())
        if not named:
            raise ValueError("A party needs a name.")
        if kind not in PARTY_KINDS:
            raise ValueError(f"One of: {', '.join(PARTY_KINDS)}.")
        party = Party(
            owner_user_id=owner_user_id,
            kind=kind,
            name=named,
            sort_name=(sort_name or "").strip() or sort_name_for(named, kind),
            contact=contact or None,
            note=(note or "").strip() or None,
        )
        self.db.add(party)
        await self.db.flush()
        return party

    async def get(self, party_id: int) -> Party | None:
        party = await self.db.get(Party, party_id)
        return party if party and party.deleted_at is None else None

    async def find(
        self,
        *,
        owner_user_id: int | None = None,
        kind: str | None = None,
        q: str | None = None,
    ) -> list[Party]:
        """Parties in filing order.

        ``find`` and not ``list``: a method named for a builtin shadows
        it inside the class, and the next annotation to say
        ``list[Party]`` resolves to this method instead of the type.
        """
        query = select(Party).where(col(Party.deleted_at).is_(None))
        if owner_user_id is not None:
            query = query.where(Party.owner_user_id == owner_user_id)
        if kind:
            query = query.where(Party.kind == kind)
        if q and q.strip():
            like = f"%{q.strip()}%"
            query = query.where(
                or_(col(Party.name).ilike(like), col(Party.sort_name).ilike(like))
            )
        return list((await self.db.exec(query.order_by(col(Party.sort_name)))).all())

    async def update(self, party_id: int, changes: dict[str, Any]) -> Party | None:
        """Rename, refile, or correct how to reach them.

        A rename does NOT refile: ``sort_name`` is what a reader looks
        under, and a correction to a display name ("Jim" to "James") is
        not a statement about where the row belongs. Refiling is its own
        edit, which is the whole reason the column is stored.
        """
        party = await self.get(party_id)
        if party is None:
            return None
        for field in ("name", "sort_name", "note"):
            if field in changes:
                value = " ".join(str(changes[field] or "").split())
                if field == "name" and not value:
                    raise ValueError("A party needs a name.")
                setattr(party, field, value or None if field == "note" else value)
        if "contact" in changes:
            party.contact = changes["contact"] or None
        party.updated_at = _utcnow()
        self.db.add(party)
        await self.db.flush()
        return party

    async def remove(self, party_id: int) -> bool:
        """Soft delete. Matters and documents point here, and a hard
        delete would take the meaning of those rows with it."""
        party = await self.get(party_id)
        if party is None:
            return False
        party.deleted_at = _utcnow()
        self.db.add(party)
        await self.db.flush()
        return True
