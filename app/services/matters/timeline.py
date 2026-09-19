"""The story of a matter, in date order, derived from rows nobody retyped.

Three years from now the papers will still be there and the story will
not. "Aug 27: the county asked. Sep 1: the POA was executed. Sep 1: the
packet went out." Without that sequence, "when did Medicaid last ask" is
archaeology across received dates and notes.

DERIVED AT READ TIME, NEVER STORED. Most of the story is already dated:
a request arrived and is due, an ask was answered, a figure is as of a
date, a paper carries the date printed on it. A stored copy of any of
that is a second home for it, and a second home is how the timeline
comes to disagree with the rows it describes. The only thing with a
table of its own is the part that leaves no paper at all - the call, the
mailing, the visit (``MatterEvent``).

One query per KIND of row, never one per row: a case with forty papers
on it reads in seven queries whatever happens (ST-10).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.schema import known, require_one_of
from app.services.finance.schemas import ChangeDisplayRow
from app.services.matters.models import MATTER_EVENT_KINDS, MatterEvent

# Ties are broken by what must have happened first: a matter is opened
# before anything is asked of it, and a deadline is the LAST thing a day
# means. Without this a request received and its own due date on one day
# read in whatever order the database felt like.
_ORDER = {
    "opened": 0,
    "asked": 1,
    "paper": 2,
    "figure": 3,
    "answered": 4,
    "due": 5,
    "closed": 6,
}


def _day(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _moment(
    on: date | None, kind: str, what: str, **rest: Any
) -> dict[str, Any] | None:
    """A moment, or nothing when it has no date. An undated row is a row
    that cannot be placed, and guessing today for it puts the past in
    the present."""
    if on is None or not what:
        return None
    return {"on": on, "kind": kind, "what": what, **rest}


class MatterEventService:
    """The events a person types, because nothing else recorded them."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(
        self,
        *,
        matter_id: int,
        occurred_at: date,
        kind: str,
        summary: str,
        document_id: int | None = None,
        party_id: int | None = None,
        owner_user_id: int | None = None,
    ) -> MatterEvent:
        require_one_of(kind, MATTER_EVENT_KINDS)
        said = " ".join((summary or "").split())
        if not said:
            raise ValueError("An event has to say what happened.")
        event = MatterEvent(
            owner_user_id=owner_user_id,
            matter_id=matter_id,
            occurred_at=occurred_at,
            kind=kind,
            summary=said,
            document_id=document_id,
            party_id=party_id,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def get(self, event_id: int) -> MatterEvent | None:
        return await self.db.get(MatterEvent, event_id)

    async def for_matter(self, matter_id: int) -> list[MatterEvent]:
        return list(
            (
                await self.db.exec(
                    select(MatterEvent)
                    .where(col(MatterEvent.matter_id) == matter_id)
                    .order_by(col(MatterEvent.occurred_at))
                )
            ).all()
        )

    async def remove(self, event_id: int) -> None:
        if (event := await self.get(event_id)) is not None:
            await self.db.delete(event)


async def timeline(db: AsyncSession, matter_id: int) -> list[dict[str, Any]]:
    """Every dated row that mentions this matter, in the order it happened.

    Each moment carries ``on``, ``kind``, ``what``, and whichever of
    ``request_id``/``item_id``/``fact_id``/``document_id``/``event_id``
    points back at the row it was read from - so a reader can get from
    the story to the thing itself rather than taking the story's word.
    """
    from app.services.matters.matters import MatterService

    matter = await MatterService(db).get(matter_id)
    if matter is None:
        return []

    moments = [
        _moment(
            _day(matter.opened_on or matter.created_at),
            "opened",
            f"{matter.title} opened",
        ),
        _moment(matter.closed_on, "closed", f"{matter.title} closed"),
        *await _asked(db, matter_id),
        *await _figures(db, matter_id),
        *await _papers(db, matter_id),
        *await _events(db, matter_id),
    ]
    return sorted(
        [one for one in moments if one],
        key=lambda m: (m["on"], _ORDER.get(m["kind"], 9)),
    )


async def _asked(db: AsyncSession, matter_id: int) -> list[dict[str, Any] | None]:
    """What a letter demanded, when it is due, and what has answered it.

    An item is dated by its FIRST evidence link: a statement attached on
    Monday and its second page on Friday is one ask answered on Monday,
    not an ask that keeps being answered.
    """
    from app.services.matters.models import EvidenceLink, RequestItem
    from app.services.matters.requests import RequestService

    requests = await RequestService(db).for_matter(matter_id)
    if not requests:
        return []
    ids = [r.id for r in requests]
    items = list(
        (
            await db.exec(
                select(RequestItem).where(col(RequestItem.request_id).in_(ids))
            )
        ).all()
    )
    links = list(
        (
            await db.exec(
                select(EvidenceLink).where(
                    col(EvidenceLink.request_item_id).in_([i.id for i in items] or [0])
                )
            )
        ).all()
    )
    first: dict[int, datetime] = {}
    for link in links:
        at = first.get(link.request_item_id)
        if at is None or link.created_at < at:
            first[link.request_item_id] = link.created_at

    counted = {one: sum(i.request_id == one for i in items) for one in ids}
    moments = []
    for request in requests:
        asks = counted.get(request.id, 0)
        moments.append(
            _moment(
                _day(request.received_on or request.created_at),
                "asked",
                f"Asked for {asks} thing{'' if asks == 1 else 's'}",
                request_id=request.id,
            )
        )
        moments.append(
            _moment(
                request.due_on,
                "due",
                f"Due back: {asks} thing{'' if asks == 1 else 's'} asked for",
                request_id=request.id,
            )
        )
    moments.extend(
        _moment(
            _day(first[item.id]),
            "answered",
            f"Answered: {item.asked}",
            item_id=item.id,
        )
        for item in items
        if item.id in first
    )
    return moments


async def _figures(db: AsyncSession, matter_id: int) -> list[dict[str, Any] | None]:
    """A figure sits on the date it is AS OF, not the date it was read.
    A balance read in September about the first of August belongs where
    the county will look for it."""
    from app.services.matters.facts import FactService, one_line

    return [
        _moment(
            _day(fact.as_of or fact.created_at),
            "figure",
            one_line(fact),
            fact_id=fact.id,
        )
        for fact in await FactService(db).find(matter_id=matter_id)
    ]


async def _papers(db: AsyncSession, matter_id: int) -> list[dict[str, Any] | None]:
    """Paper is dated by what is PRINTED on it, falling back to when it
    arrived: a letter dated the 27th that was scanned in October is an
    episode of August."""
    from app.services.documents.service import DocumentService
    from app.services.matters.models import matter_tag

    documents, _ = await DocumentService(db).list_documents(
        tag=matter_tag(matter_id), page_size=200
    )
    return [
        _moment(
            _day(document.document_date or document.received_at),
            "paper",
            document.title,
            document_id=document.id,
        )
        for document in documents
    ]


async def _events(db: AsyncSession, matter_id: int) -> list[dict[str, Any] | None]:
    return [
        _moment(event.occurred_at, event.kind, event.summary, event_id=event.id)
        for event in await MatterEventService(db).for_matter(matter_id)
    ]


class RecordEventPayload(BaseModel):
    """What happened on a case that left no paper, proposed rather than
    written: she never writes, so this goes on a card like everything
    else and the timeline only gains it when somebody approves.

    The DAY it happened, never today: the thing being recorded is
    usually being told to her after the fact - "I called them on
    Tuesday" - and filing it under the day of the telling puts the story
    out of order, which is the one thing a timeline must not do.
    """

    model_config = ConfigDict(extra="forbid")

    matter_id: int
    occurred_at: date
    kind: str = "note"
    summary: str
    # Who it was with, where that is somebody on file. A call to the
    # county is a call to a party, and naming them here is what lets the
    # story be read from their side later.
    party_id: int | None = None
    document_id: int | None = None

    _known_kind = field_validator("kind")(known(MATTER_EVENT_KINDS))

    @field_validator("summary")
    @classmethod
    def _says_something(cls, value: str) -> str:
        if not " ".join((value or "").split()):
            raise ValueError("An event has to say what happened.")
        return value


async def _matter_and_party(
    db: AsyncSession, payload: RecordEventPayload
) -> tuple[Any, Any]:
    from app.services.matters.matters import MatterService
    from app.services.matters.service import PartyService

    matter = await MatterService(db).get(payload.matter_id)
    if matter is None:
        raise ValueError(f"No matter with id {payload.matter_id}")
    party = (
        await PartyService(db).get(payload.party_id)
        if payload.party_id is not None
        else None
    )
    if payload.party_id is not None and party is None:
        raise ValueError(f"No contact with id {payload.party_id}")
    return matter, party


async def record_event_execute(
    db: AsyncSession, payload: RecordEventPayload, owner_user_id: int | None
) -> dict[str, Any]:
    await _matter_and_party(db, payload)
    event = await MatterEventService(db).add(
        matter_id=payload.matter_id,
        occurred_at=payload.occurred_at,
        kind=payload.kind,
        summary=payload.summary,
        document_id=payload.document_id,
        party_id=payload.party_id,
        owner_user_id=owner_user_id,
    )
    return {"event_id": event.id, "matter_id": payload.matter_id}


async def record_event_describe(
    db: AsyncSession, payload: RecordEventPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    matter, party = await _matter_and_party(db, payload)
    rows = [
        ChangeDisplayRow(label="Matter", value=matter.title),
        ChangeDisplayRow(label="When", value=payload.occurred_at.isoformat()),
        ChangeDisplayRow(label="Sort", value=payload.kind),
        ChangeDisplayRow(label="What", value=" ".join(payload.summary.split())),
    ]
    if party is not None:
        rows.append(ChangeDisplayRow(label="With", value=party.name))
    return rows
