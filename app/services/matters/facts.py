"""Facts: what you can say about someone's money, and how you know.

The number is the easy half. A $2,075.00 deposit in the register and a
benefit letter saying $2,180.40 GROSS are both true and only one of them
answers the county, so every row carries where it came from and nothing
here tries to reconcile the two. Recording that they disagree is the
feature; deciding which is right is the reader's.

Corrections supersede. Overwriting a figure loses what you told an
agency last year, which is the one thing you may later have to defend.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.core.schema import require_one_of
from app.services.matters.models import (
    FACT_ATTRIBUTES,
    FACT_PERIODS,
    FACT_PROVENANCE,
    Fact,
)

ATTRIBUTE_KEYS = tuple(key for key, _ in FACT_ATTRIBUTES)
LABELS = dict(FACT_ATTRIBUTES)

# Days in an average year, over twelve months. A daily rate times 30 is
# eleven days short by December, and a benefit that quotes per day is
# quoting against the calendar, not against a round month.
DAYS_PER_MONTH = Decimal("365.25") / 12
PER_MONTH: dict[str, Decimal] = {
    "day": DAYS_PER_MONTH,
    "week": DAYS_PER_MONTH / 7,
    "month": Decimal(1),
    "year": Decimal(1) / 12,
}


class FactService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self,
        *,
        subject_party_id: int,
        attribute: str,
        account_id: int | None = None,
        value_cents: int | None = None,
        period: str = "once",
        text_value: str | None = None,
        label: str | None = None,
        as_of: date | None = None,
        provenance: str = "stated",
        document_id: int | None = None,
        page: int | None = None,
        source_party_id: int | None = None,
        source_note: str | None = None,
        source_url: str | None = None,
        verified: bool = False,
        matter_id: int | None = None,
        owner_user_id: int | None = None,
        note: str | None = None,
    ) -> Fact:
        """One claim, with where it came from.

        A fact with no value at all is not a fact - an attribute and a
        date with nothing in between says only that somebody opened a
        form.
        """
        require_one_of(attribute, ATTRIBUTE_KEYS)
        require_one_of(period, FACT_PERIODS)
        require_one_of(provenance, FACT_PROVENANCE)
        written = (text_value or "").strip() or None
        if value_cents is None and written is None:
            raise ValueError("Give the fact a figure, or say it in words.")
        if provenance == "document" and document_id is None:
            raise ValueError("A document-derived fact needs the document.")
        fact = Fact(
            owner_user_id=owner_user_id,
            subject_party_id=subject_party_id,
            matter_id=matter_id,
            account_id=account_id,
            attribute=attribute,
            label=(label or "").strip() or None,
            value_cents=value_cents,
            period=period,
            text_value=written,
            as_of=as_of,
            provenance=provenance,
            document_id=document_id,
            page=page,
            source_party_id=source_party_id,
            source_note=(source_note or "").strip() or None,
            source_url=web_address(source_url),
            verified=verified,
            note=(note or "").strip() or None,
        )
        self.db.add(fact)
        await self.db.flush()
        return fact

    async def get(self, fact_id: int) -> Fact | None:
        fact = await self.db.get(Fact, fact_id)
        return fact if fact and fact.deleted_at is None else None

    async def find(
        self,
        *,
        subject_party_id: int | None = None,
        source_party_id: int | None = None,
        matter_id: int | None = None,
        account_id: int | None = None,
        attribute: str | None = None,
        standing: bool = True,
    ) -> list[Fact]:
        """Facts, newest first.

        ``standing`` drops the ones a correction has replaced: what you
        would say TODAY, which is what a page and a tool both want. The
        superseded rows are still there for anyone asking what you said
        last year.
        """
        query = select(Fact).where(col(Fact.deleted_at).is_(None))
        if subject_party_id is not None:
            query = query.where(Fact.subject_party_id == subject_party_id)
        if source_party_id is not None:
            # What this place SAYS, as opposed to what is said about it.
            query = query.where(Fact.source_party_id == source_party_id)
        if matter_id is not None:
            query = query.where(Fact.matter_id == matter_id)
        if account_id is not None:
            query = query.where(Fact.account_id == account_id)
        if attribute:
            query = query.where(Fact.attribute == attribute)
        if standing:
            query = query.where(col(Fact.superseded_by_id).is_(None))
        return list(
            (
                await self.db.exec(
                    query.order_by(col(Fact.as_of).desc(), col(Fact.id).desc())
                )
            ).all()
        )

    async def supersede(self, fact_id: int, **fields: Any) -> Fact | None:
        """Correct a figure by writing a new one and pointing the old at
        it. The old row keeps saying what it always said, which is what
        somebody defending a filing needs it to do."""
        old = await self.get(fact_id)
        if old is None:
            return None
        carried: dict[str, Any] = {
            "subject_party_id": old.subject_party_id,
            "matter_id": old.matter_id,
            "account_id": old.account_id,
            "attribute": old.attribute,
            "label": old.label,
            "value_cents": old.value_cents,
            "period": old.period,
            "text_value": old.text_value,
            "as_of": old.as_of,
            "provenance": old.provenance,
            "document_id": old.document_id,
            "page": old.page,
            "source_party_id": old.source_party_id,
            "source_note": old.source_note,
            "source_url": old.source_url,
            "verified": old.verified,
            "owner_user_id": old.owner_user_id,
            "note": old.note,
        }
        carried.update(fields)
        fresh = await self.record(**carried)
        old.superseded_by_id = fresh.id
        old.updated_at = utcnow()
        self.db.add(old)
        await self.db.flush()
        return fresh

    async def verify(self, fact_id: int, verified: bool = True) -> Fact | None:
        """Somebody checked this against its source. Never set by
        extraction: the whole point of the flag is that a person looked."""
        fact = await self.get(fact_id)
        if fact is None:
            return None
        fact.verified = verified
        fact.updated_at = utcnow()
        self.db.add(fact)
        await self.db.flush()
        return fact

    async def forget(self, fact_id: int) -> bool:
        fact = await self.get(fact_id)
        if fact is None:
            return False
        fact.deleted_at = utcnow()
        self.db.add(fact)
        await self.db.flush()
        return True


def web_address(raw: str | None) -> str | None:
    """A link worth putting in an href, or nothing.

    http and https only. A stored address is rendered as a link the
    reader clicks, and ``javascript:`` in an href is a script the page
    runs - so the scheme is checked on the way IN, where there is one
    place to check it rather than one per template.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    head = cleaned.split("/", 1)[0]
    if "://" not in cleaned:
        # A bare host is the usual paste, and typing the scheme is the
        # kind of thing that makes somebody not bother. Anything else
        # carrying a colon is naming a scheme we do not take.
        if ":" in head:
            raise ValueError("A link has to be an http or https address.")
        cleaned = f"https://{cleaned}"
    if cleaned.split("://", 1)[0].lower() not in ("http", "https"):
        raise ValueError("A link has to be an http or https address.")
    return cleaned[:500]


def monthly_cents(value_cents: int | None, period: str) -> int | None:
    """What a rate comes to in a month, or None if it is not a rate.

    Arithmetic done at the point of READING. The portal quotes a day and
    the county asks for a month; storing the multiplication would file a
    calculation of ours as a quotation of theirs.
    """
    factor = PER_MONTH.get(period)
    if value_cents is None or factor is None:
        return None
    if period == "month":
        return value_cents
    return int(
        (Decimal(value_cents) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


async def place_book(db: AsyncSession) -> dict[int, dict[str, str]]:
    """The address book as places: id -> name and website.

    One lookup for every surface that draws a source or a sign-in, so
    "the pension portal" is the same row and the same address wherever
    it is named.
    """
    from app.services.matters.service import PartyService

    return {
        party.id: {
            "name": party.name,
            "website": str((party.contact or {}).get("website") or ""),
        }
        for party in await PartyService(db).find()
        if party.id is not None
    }


def drawn(
    fact: Fact, places: dict[int, dict[str, str]] | None = None
) -> dict[str, Any]:
    """One fact as a page or a tool says it."""
    place = (places or {}).get(fact.source_party_id or -1, {})
    return {
        "id": fact.id,
        "subject_party_id": fact.subject_party_id,
        "matter_id": fact.matter_id,
        "account_id": fact.account_id,
        "subject": (places or {}).get(fact.subject_party_id, {}).get("name", ""),
        "attribute": fact.attribute,
        "attribute_label": LABELS.get(fact.attribute, fact.attribute),
        "label": fact.label or "",
        "value_cents": fact.value_cents,
        "period": fact.period,
        "monthly_cents": monthly_cents(fact.value_cents, fact.period),
        "text_value": fact.text_value or "",
        "as_of": fact.as_of,
        "provenance": fact.provenance,
        "document_id": fact.document_id,
        "page": fact.page,
        "source_party_id": fact.source_party_id,
        # The name of the place, and the best address for it: the exact
        # page if one was given, otherwise the place's own website.
        "source_place": place.get("name", ""),
        "source_site": fact.source_url or place.get("website", ""),
        "source_note": fact.source_note or "",
        "source_url": fact.source_url or "",
        "verified": fact.verified,
        "note": fact.note or "",
    }


assert set(PER_MONTH) <= set(FACT_PERIODS)
