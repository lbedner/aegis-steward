"""Which paper answers which ask, read from either end.

Evidence and answers are many-to-many in both directions, so the link is
a row rather than a column. ``RequestItem.document_id`` held one
document, which made "one statement satisfies three asks" work by
accident and "one ask needs three letters" impossible.

An item's status is DERIVED from its links. Attaching is the answer -
somebody who has found the power of attorney and filed it here is not
then asked to say separately that the item is satisfied - and removing
the last piece of evidence reopens it, because an item whose only
evidence has been taken away is not answered. Removing one of three
leaves two, and two is still an answer.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.models import EvidenceLink


async def _item(db: AsyncSession, request_item_id: int) -> Any:
    from app.services.matters.models import RequestItem

    item = await db.get(RequestItem, request_item_id)
    if item is None:
        raise ValueError(f"No ask with id {request_item_id}")
    return item


async def link(
    db: AsyncSession,
    request_item_id: int,
    *,
    document_id: int | None = None,
    page: int | None = None,
    fact_id: int | None = None,
    note: str | None = None,
) -> EvidenceLink:
    """File a piece of evidence against an ask, and mark it answered.

    Exactly one of ``document_id`` or ``fact_id``: a link naming neither
    points at no evidence, and one naming both cannot say which answered
    the ask.

    Idempotent on (item, document, page, fact). Re-reading a document,
    or two people filing the same answer, must not double it - but the
    same paper on two PAGES is two links, because which page is the
    whole reason for recording it.
    """
    from sqlmodel import col, select

    if bool(document_id) == bool(fact_id):
        raise ValueError(
            "Evidence is a document or a fact, exactly one. A link naming "
            "neither points at nothing; one naming both cannot say which "
            "answered the ask."
        )
    await _item(db, request_item_id)

    existing = (
        await db.exec(
            select(EvidenceLink)
            .where(EvidenceLink.request_item_id == request_item_id)
            .where(
                col(EvidenceLink.document_id).is_(None)
                if document_id is None
                else EvidenceLink.document_id == document_id
            )
            .where(
                col(EvidenceLink.page).is_(None)
                if page is None
                else EvidenceLink.page == page
            )
            .where(
                col(EvidenceLink.fact_id).is_(None)
                if fact_id is None
                else EvidenceLink.fact_id == fact_id
            )
        )
    ).first()
    if existing is not None:
        # A second filing may carry the note the first one lacked.
        if note and not existing.note:
            existing.note = note
            db.add(existing)
            await db.flush()
        return existing

    row = EvidenceLink(
        request_item_id=request_item_id,
        document_id=document_id,
        page=page,
        fact_id=fact_id,
        note=note,
    )
    db.add(row)
    await db.flush()
    await _derive(db, request_item_id)
    return row


async def unlink(
    db: AsyncSession,
    request_item_id: int,
    *,
    document_id: int | None = None,
    page: int | None = None,
    fact_id: int | None = None,
) -> bool:
    """Take a piece of evidence off. False when it was not there.

    The item reopens only when the LAST one goes: two of three removed
    still leaves an answer, and reopening early would be a lie about
    what is outstanding.
    """
    from sqlmodel import select

    query = select(EvidenceLink).where(EvidenceLink.request_item_id == request_item_id)
    if document_id is not None:
        query = query.where(EvidenceLink.document_id == document_id)
    if fact_id is not None:
        query = query.where(EvidenceLink.fact_id == fact_id)
    if page is not None:
        query = query.where(EvidenceLink.page == page)
    rows = list((await db.exec(query)).all())
    if not rows:
        return False
    for row in rows:
        await db.delete(row)
    await db.flush()
    await _derive(db, request_item_id)
    return True


async def satisfied_by(db: AsyncSession, request_item_id: int) -> list[EvidenceLink]:
    """Everything filed against this ask, oldest first."""
    from sqlmodel import col, select

    return list(
        (
            await db.exec(
                select(EvidenceLink)
                .where(EvidenceLink.request_item_id == request_item_id)
                .order_by(col(EvidenceLink.id))
            )
        ).all()
    )


async def satisfied_by_many(
    db: AsyncSession, item_ids: list[int]
) -> dict[int, list[EvidenceLink]]:
    """Everything filed against each of these asks, in one query.

    ``satisfied_by`` asked per item, which is fine from an item's own
    page and is a query per row anywhere that draws a whole matter - the
    answer sheet did exactly that, and so would the timeline if it had
    not written its own copy of this (2026-09-18).
    """
    from sqlmodel import col, select

    if not item_ids:
        return {}
    found: dict[int, list[EvidenceLink]] = {item_id: [] for item_id in item_ids}
    links = (
        await db.exec(
            select(EvidenceLink)
            .where(col(EvidenceLink.request_item_id).in_(item_ids))
            .order_by(col(EvidenceLink.id))
        )
    ).all()
    for link in links:
        found.setdefault(link.request_item_id, []).append(link)
    return found


async def answers_for(db: AsyncSession, document_id: int) -> list[EvidenceLink]:
    """The asks this document answers. The same relation, read from the
    other end: the document view shows what it satisfies, the request
    view shows what each item is answered by."""
    from sqlmodel import col, select

    return list(
        (
            await db.exec(
                select(EvidenceLink)
                .where(EvidenceLink.document_id == document_id)
                .order_by(col(EvidenceLink.id))
            )
        ).all()
    )


async def _derive(db: AsyncSession, request_item_id: int) -> None:
    """An item's status follows its links.

    Only ever between "needed" and "satisfied". A person who marked an
    item ``waived`` or ``not_applicable`` has said something evidence
    does not overrule - filing a document against a waived ask should
    not quietly un-waive it.
    """
    from app.services.matters.models import RequestItem

    item = await db.get(RequestItem, request_item_id)
    if item is None or item.status in ("waived", "not_applicable"):
        return
    wanted = "satisfied" if await satisfied_by(db, request_item_id) else "needed"
    if item.status != wanted:
        item.status = wanted
        db.add(item)
        await db.flush()
