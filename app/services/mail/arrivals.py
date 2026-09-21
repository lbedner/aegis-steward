"""What came in: one answer for the page and for the chat (MI-06).

The approvals queue says what is WAITING; this says what ARRIVED and
what each message became - a letter, its attachments, a card - and,
when nothing, why. "Nothing happened" with no reason is
indistinguishable from a broken import. One set of rows, read by the
intake page and by the assistant's ``arrivals`` tool, so the two
cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.mail.ingest import cards_by_address
from app.services.mail.models import MailAttachment, MailMessage
from app.services.matters.models.core import Party


@dataclass(frozen=True)
class Paper:
    document_id: int
    filename: str


@dataclass(frozen=True)
class Arrival:
    """One message and what it became."""

    id: int
    received_at: datetime
    sent_at: datetime | None
    sender: str  # the display name, else the address
    address: str
    subject: str
    party: dict[str, Any] | None  # {id, name} when filed under someone
    letter_id: int | None
    attachments: list[Paper]
    card: str | None  # the contact.create card's status for this address
    became: str  # one sentence a person can read

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "received_at": self.received_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "sender": self.sender,
            "address": self.address,
            "subject": self.subject,
            "party": self.party,
            "letter_id": self.letter_id,
            "attachment_ids": [a.document_id for a in self.attachments],
            "card": self.card,
            "became": self.became,
        }


@dataclass
class Day:
    date: date
    messages: list[Arrival] = field(default_factory=list)


async def arrivals(
    db: AsyncSession, *, owner_user_id: int | None, limit: int = 500
) -> list[Day]:
    """Everything that came in, grouped by the day it arrived, newest
    first. Four queries for the whole listing, never one per row."""
    query = (
        select(MailMessage)
        .where(MailMessage.owner_user_id == owner_user_id)
        .order_by(col(MailMessage.created_at).desc(), col(MailMessage.id).desc())
        .limit(limit)
    )
    rows = list((await db.exec(query)).all())
    if not rows:
        return []
    ids = [int(r.id) for r in rows]
    papers: dict[int, list[Paper]] = {}
    for link in (
        await db.exec(
            select(MailAttachment).where(col(MailAttachment.message_id).in_(ids))
        )
    ).all():
        papers.setdefault(link.message_id, []).append(
            Paper(link.document_id, link.filename)
        )
    party_ids = {r.party_id for r in rows if r.party_id is not None}
    names = (
        {
            int(p.id): p.name
            for p in (
                await db.exec(select(Party).where(col(Party.id).in_(party_ids)))
            ).all()
        }
        if party_ids
        else {}
    )
    cards = await cards_by_address(db)

    days: dict[date, Day] = {}
    for row in rows:
        arrival = _arrival(row, papers.get(int(row.id), []), names, cards)
        days.setdefault(
            arrival.received_at.date(), Day(arrival.received_at.date())
        ).messages.append(arrival)
    return [days[d] for d in sorted(days, reverse=True)]


def _arrival(
    row: MailMessage, papers: list[Paper], names: dict[int, str], cards: dict[str, str]
) -> Arrival:
    party = (
        {"id": row.party_id, "name": names.get(row.party_id, "")}
        if row.party_id is not None
        else None
    )
    sender = row.from_name or row.from_address
    return Arrival(
        id=int(row.id),
        received_at=row.created_at,
        sent_at=row.sent_at,
        sender=sender,
        address=row.from_address,
        subject=row.subject or "(no subject)",
        party=party,
        letter_id=row.document_id,
        attachments=papers,
        card=cards.get(row.from_address),
        became=_became(row, papers, party, cards.get(row.from_address), sender),
    )


def _became(
    row: MailMessage,
    papers: list[Paper],
    party: dict[str, Any] | None,
    card: str | None,
    sender: str,
) -> str:
    """What the message turned into, said once. A message that produced
    nothing says why: with no reason it reads as a broken import."""
    if row.document_id is None and not papers:
        return "nothing: empty message, no attachments"
    parts = ["letter"] if row.document_id is not None else []
    if papers:
        n = len(papers)
        parts.append(f"{n} attachment{'s' if n != 1 else ''}")
    what = " + ".join(parts)
    if party is not None:
        return f"{what}, filed under {party['name']}"
    if card == "pending":
        return f"{what}; a card offers to add {sender}"
    if card == "rejected":
        return f"{what}; {sender} was declined as a contact"
    return what
