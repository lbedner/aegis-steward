"""Opening a matter, and saying who is in it.

Separate from ``service.py`` because a party is an address-book row and
a matter is a case: they are read together and changed apart, and the
500-line budget is easier to keep honest when a file is one subject.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.models import (
    DOCUMENT_PARTY_ROLES,
    MATTER_STATUSES,
    PARTICIPANT_ROLES,
    DocumentParty,
    Matter,
    MatterParticipant,
    Party,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MatterService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def open(
        self,
        *,
        title: str,
        kind: str | None = None,
        reference: str | None = None,
        subject_party_id: int | None = None,
        counterpart_party_id: int | None = None,
        owner_user_id: int | None = None,
        opened_on: date | None = None,
        note: str | None = None,
    ) -> Matter:
        named = " ".join((title or "").split())
        if not named:
            raise ValueError("A matter needs a title.")
        matter = Matter(
            owner_user_id=owner_user_id,
            title=named,
            kind=(kind or "").strip() or None,
            reference=(reference or "").strip() or None,
            subject_party_id=subject_party_id,
            counterpart_party_id=counterpart_party_id,
            status="open",
            opened_on=opened_on,
            note=(note or "").strip() or None,
        )
        self.db.add(matter)
        await self.db.flush()
        return matter

    async def get(self, matter_id: int) -> Matter | None:
        matter = await self.db.get(Matter, matter_id)
        return matter if matter and matter.deleted_at is None else None

    async def by_reference(self, reference: str) -> Matter | None:
        """The matter an agency's own case number names.

        How a second letter lands on the first letter's case: the number
        on the page is the one thing both letters agree about.
        """
        cleaned = (reference or "").strip()
        if not cleaned:
            return None
        return (
            await self.db.exec(
                select(Matter).where(
                    Matter.reference == cleaned, col(Matter.deleted_at).is_(None)
                )
            )
        ).first()

    async def find(
        self, *, owner_user_id: int | None = None, status: str | None = None
    ) -> list[Matter]:
        query = select(Matter).where(col(Matter.deleted_at).is_(None))
        if owner_user_id is not None:
            query = query.where(Matter.owner_user_id == owner_user_id)
        if status:
            query = query.where(Matter.status == status)
        return list(
            (await self.db.exec(query.order_by(col(Matter.opened_on).desc()))).all()
        )

    async def for_party(self, party_id: int) -> list[tuple[Matter, str]]:
        """The cases this party is in, and as what - newest first. One
        query: the role is on the link, the case on the row."""
        rows = await self.db.exec(
            select(Matter, MatterParticipant.role)
            .join(MatterParticipant, MatterParticipant.matter_id == Matter.id)
            .where(MatterParticipant.party_id == party_id)
            .where(col(Matter.deleted_at).is_(None))
            .order_by(col(Matter.opened_on).desc())
        )
        return [(matter, role) for matter, role in rows.all()]

    async def set_status(
        self, matter_id: int, status: str, on: date | None = None
    ) -> Matter | None:
        if status not in MATTER_STATUSES:
            raise ValueError(f"One of: {', '.join(MATTER_STATUSES)}.")
        matter = await self.get(matter_id)
        if matter is None:
            return None
        matter.status = status
        matter.closed_on = on if status == "closed" else None
        matter.updated_at = _utcnow()
        self.db.add(matter)
        await self.db.flush()
        return matter

    async def add_participant(
        self, matter_id: int, party_id: int, role: str, note: str | None = None
    ) -> MatterParticipant:
        """Put a party in a matter, as something.

        The role is per matter. Adding the same party twice under two
        roles is normal - a representative who is also the subject's son
        - so only (matter, party, role) is unique.
        """
        if role not in PARTICIPANT_ROLES:
            raise ValueError(f"One of: {', '.join(PARTICIPANT_ROLES)}.")
        link = MatterParticipant(
            matter_id=matter_id, party_id=party_id, role=role, note=note
        )
        self.db.add(link)
        await self.db.flush()
        return link

    async def participants(
        self, matter_id: int
    ) -> list[tuple[MatterParticipant, Party]]:
        rows = (
            await self.db.exec(
                select(MatterParticipant, Party)
                .where(MatterParticipant.matter_id == matter_id)
                .where(Party.id == MatterParticipant.party_id)
                .order_by(col(MatterParticipant.role))
            )
        ).all()
        return [(link, party) for link, party in rows]

    async def name_document(
        self, document_id: int, party_id: int, role: str
    ) -> DocumentParty:
        """Who wrote this document, or whom it is about."""
        if role not in DOCUMENT_PARTY_ROLES:
            raise ValueError(f"One of: {', '.join(DOCUMENT_PARTY_ROLES)}.")
        link = DocumentParty(document_id=document_id, party_id=party_id, role=role)
        self.db.add(link)
        await self.db.flush()
        return link

    async def document_parties(self, document_id: int) -> list[tuple[str, Party]]:
        rows = (
            await self.db.exec(
                select(DocumentParty, Party)
                .where(DocumentParty.document_id == document_id)
                .where(Party.id == DocumentParty.party_id)
            )
        ).all()
        return [(link.role, party) for link, party in rows]


async def summarised(db: AsyncSession, matter: Matter) -> dict[str, Any]:
    """One matter as a page draws it: who is in it, and under what."""
    service = MatterService(db)
    return {
        "id": matter.id,
        "title": matter.title,
        "kind": matter.kind or "",
        "reference": matter.reference or "",
        "status": matter.status,
        "opened_on": matter.opened_on,
        "participants": [
            {"role": link.role, "party": party.name, "party_id": party.id}
            for link, party in await service.participants(matter.id)
        ],
    }
