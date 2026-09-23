"""Opening a matter, and saying who is in it.

Separate from ``service.py`` because a party is an address-book row and
a matter is a case: they are read together and changed apart, and the
500-line budget is easier to keep honest when a file is one subject.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.core.schema import require_one_of
from app.services.matters.models import (
    DOCUMENT_PARTY_ROLES,
    MATTER_CADENCES,
    MATTER_STATUSES,
    MATTER_TAG_PREFIX,
    PARTICIPANT_ROLES,
    DocumentParty,
    Matter,
    MatterParticipant,
    Party,
)

# How far each cadence moves the next expected date, in months.
_CADENCE_MONTHS = {"annual": 12, "semiannual": 6, "quarterly": 3}


def next_after(expected: date, cadence: str) -> date:
    """The next expected date after ``expected``, one cadence on.

    By calendar month, so Aug 1 stays Aug 1; a day that month lacks
    (Jan 31 + one quarter) lands on the month's last day instead.
    """
    import calendar

    total = expected.month - 1 + _CADENCE_MONTHS[cadence]
    year, month = expected.year + total // 12, total % 12 + 1
    return date(year, month, min(expected.day, calendar.monthrange(year, month)[1]))


async def suggested_next(
    db: AsyncSession, matter_id: int, cadence: str, *, today: date | None = None
) -> date | None:
    """When the next request should arrive, counted from the last one.

    Agencies send renewals on a cycle, so the next letter comes about a
    cadence after the last one ARRIVED. With no letter received, the day
    the matter opened; with neither, nothing. Stepped forward past today:
    a suggestion already behind us is no suggestion. It is only offered -
    the dialog shows it and the person saves it.
    """
    from app.services.finance.utils import current_date
    from app.services.matters.requests import RequestService

    today = today or current_date()
    matter = await MatterService(db).get(matter_id)
    if matter is None:
        return None
    received = [
        one.received_on
        for one in await RequestService(db).for_matter(matter_id)
        if one.received_on is not None
    ]
    anchor = max(received) if received else matter.opened_on
    if anchor is None:
        return None
    upcoming = next_after(anchor, cadence)
    while upcoming <= today:
        upcoming = next_after(upcoming, cadence)
    return upcoming


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

    async def set_cadence(
        self,
        matter_id: int,
        *,
        cadence: str | None,
        next_expected_on: date | None,
    ) -> Matter | None:
        """When the next request is due to arrive, and how often one does.

        Setting the date again IS snoozing: there is no reminder state
        beside it. A date with no cadence is a one-off expectation - it
        clears when that letter comes rather than rolling on.
        """
        if cadence is not None:
            require_one_of(cadence, MATTER_CADENCES)
        matter = await self.get(matter_id)
        if matter is None:
            return None
        matter.cadence = cadence
        matter.next_expected_on = next_expected_on
        matter.updated_at = utcnow()
        self.db.add(matter)
        await self.db.flush()
        return matter

    async def set_status(
        self, matter_id: int, status: str, on: date | None = None
    ) -> Matter | None:
        require_one_of(status, MATTER_STATUSES)
        matter = await self.get(matter_id)
        if matter is None:
            return None
        matter.status = status
        matter.closed_on = on if status == "closed" else None
        matter.updated_at = utcnow()
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
        require_one_of(role, PARTICIPANT_ROLES)
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
        require_one_of(role, DOCUMENT_PARTY_ROLES)
        link = DocumentParty(document_id=document_id, party_id=party_id, role=role)
        self.db.add(link)
        await self.db.flush()
        return link

    async def for_document(self, document_id: int) -> int | None:
        """The matter a document is filed on, or None.

        The other direction of ``matter_tag``, and a query rather than a
        model: a document knows nothing about matters, so the answer is
        read off the tag whose shape matters owns.
        """
        from app.services.documents.service import DocumentService

        filed = await DocumentService(self.db).tags_for(document_id)
        ids = [
            tag.removeprefix(MATTER_TAG_PREFIX)
            for tag in filed
            if tag.startswith(MATTER_TAG_PREFIX)
        ]
        return next((int(one) for one in ids if one.isdigit()), None)

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
        "cadence": matter.cadence,
        "next_expected_on": matter.next_expected_on,
        "participants": [
            {"role": link.role, "party": party.name, "party_id": party.id}
            for link, party in await service.participants(matter.id)
        ],
    }
