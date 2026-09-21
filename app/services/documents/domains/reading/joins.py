"""What a day's arrivals answer in each other.

``proposals.py`` reads one document and proposes what IT says - its
name, its sender, the figure on its face. That narrowness is
deliberate: a reading that guesses beyond its own page is the failure
this whole surface exists to avoid.

But a day's post is not a set of unrelated pages. The county's letter
asks for three things and the statement beside it answers one of them,
and joining those is what a person does by hand at a kitchen table.
This pass proposes the joins, and nothing else:

- ``document.evidence_link`` - this paper answers that ask, because the
  figure it states is the attribute the ask names.
- ``ask.amend`` - an ask nobody mapped, named by its own wording.

No model call. A join is only ever made where the two sides already
agree in writing: the figure's attribute equals the ask's attribute,
or the ask's own words contain exactly one attribute's name. Every
card cites the page and the line, because an approver looking at
"this statement answers proof of income" cannot judge it, and one
looking at "page 2: Net Benefit: $1004.93" has the whole question in
front of them.

Idempotent: a pass that ran yesterday proposes nothing today. The
caller passes only what is NEW, and a card already waiting for the
same pair is the answer already waiting.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_async_session
from app.core.log import logger

EVIDENCE = "document.evidence_link"
AMEND = "ask.amend"
FIGURE = "fact.record"
PROPOSED_BY = "reading"

# An ask is answerable while nobody has answered it.
OPEN = "needed"


async def propose_joins(
    db: AsyncSession, *, document_ids: list[int], owner_user_id: int | None = None
) -> list[Any]:
    """Join the given arrivals to the asks they answer. Returns the cards.

    Guarded per document, because the other arrivals are the valuable
    thing: one paper whose join falls over must not take the rest of
    the morning with it.
    """
    made: list[Any] = []
    for document_id in document_ids:
        try:
            made.extend(await _join_one(db, document_id, owner_user_id))
        except Exception:
            logger.exception("Joining %s answered nothing", document_id)
    return made


# How far back a pass looks. Not "since yesterday": a run that was
# missed, or a container that was down over a weekend, must still catch
# what arrived while nobody was reading. Re-proposing is a no-op, so
# the only cost of the window is the query.
RECENT_DAYS = 3


async def join_recent_arrivals(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> list[Any]:
    """The day's pass: join what has arrived lately to the asks open on
    its matter. Idempotent, so firing late or twice changes nothing."""
    from datetime import timedelta

    from sqlmodel import col, select

    from app.core.clock import utcnow
    from app.services.documents.models import Document

    # The app's one clock: stamps are stored naive UTC, and an aware
    # bound parameter compares against them as a different string.
    since = utcnow() - timedelta(days=RECENT_DAYS)
    rows = (
        await db.exec(
            select(Document.id).where(
                col(Document.deleted_at).is_(None),
                col(Document.received_at) >= since,
            )
        )
    ).all()
    return await propose_joins(
        db, document_ids=[int(one) for one in rows], owner_user_id=owner_user_id
    )


async def join_arrivals_job() -> None:
    """The scheduled pass. Nightly, after the day's post has been read.

    Owns its session: a scheduler fires with no request behind it, and
    a pass that proposes without committing is a pass nobody sees. Safe
    to fire late or twice - every proposal here is deduped against the
    cards already waiting.
    """
    async with get_async_session() as db:
        made = await join_recent_arrivals(db, owner_user_id=None)
        await db.commit()
    logger.info("Arrivals joined: %d proposed", len(made))


async def _join_one(
    db: AsyncSession, document_id: int, owner_user_id: int | None
) -> list[Any]:
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    matter_id = await MatterService(db).for_document(document_id)
    if matter_id is None:
        # A statement nobody asked for is on the shelf and not an answer.
        return []
    figure = await _figure_stated(db, document_id)
    requests = RequestService(db)
    letters = await requests.for_matter(matter_id)
    items = [
        item
        for group in (await requests.items_of([int(r.id) for r in letters])).values()
        for item in group
        if item.status == OPEN
    ]
    if not items:
        return []

    pending = await _already_proposed(db)
    made: list[Any] = []
    for item in items:
        if item.ask is None:
            card = await _name_the_ask(db, item, pending, owner_user_id)
        elif figure is not None and item.ask == figure.payload.get("attribute"):
            card = await _answer_the_ask(
                db, document_id, item, figure, pending, owner_user_id
            )
        else:
            card = None
        if card is not None:
            made.append(card)
    if made and figure is not None:
        await _put_the_figure_on_the_matter(db, figure, matter_id)
    return made


async def _put_the_figure_on_the_matter(
    db: AsyncSession, figure: Any, matter_id: int
) -> None:
    """Finish the figure's own card with what this pass knows.

    ``propose_figure`` reads one document and cannot say which matter
    the number belongs to - it is looking at a statement, not a case.
    The sheet only prints a figure that is tied to the ask, and a fact
    on no matter is tied to nothing, so approving both cards left the
    ask answered with a blank where the number should be.

    The card is still pending and still the same proposal; this adds
    the one field the reader had no way to fill.
    """
    if figure.payload.get("matter_id"):
        return
    figure.payload = {**figure.payload, "matter_id": matter_id}
    db.add(figure)
    await db.flush()


async def _figure_stated(db: AsyncSession, document_id: int) -> Any | None:
    """What this document says it is about, as the reading already
    proposed it. Read off the card rather than the page a second time:
    the figure, its attribute, its page and its line are all there, and
    two readings of one number is two facts about one thing.
    """
    from app.services.finance.domains.writes.queue import list_changes

    for change in await list_changes(db, status="pending"):
        if change.change_type != FIGURE:
            continue
        payload = change.payload or {}
        if payload.get("document_id") == document_id and payload.get("page"):
            return change
    return None


async def _already_proposed(db: AsyncSession) -> set[tuple[str, Any, Any]]:
    """Every join already waiting, so a second pass over the same
    arrival does not stack a card on top of one."""
    from app.services.finance.domains.writes.queue import list_changes

    waiting: set[tuple[str, Any, Any]] = set()
    for change in await list_changes(db, status="pending"):
        payload = change.payload or {}
        if change.change_type == EVIDENCE:
            waiting.add(
                (EVIDENCE, payload.get("document_id"), payload.get("request_item_id"))
            )
        elif change.change_type == AMEND:
            waiting.add((AMEND, payload.get("item_id"), None))
    return waiting


async def _answer_the_ask(
    db: AsyncSession,
    document_id: int,
    item: Any,
    figure: Any,
    pending: set[tuple[str, Any, Any]],
    owner_user_id: int | None,
) -> Any | None:
    from app.services.finance.domains.writes.queue import propose

    key = (EVIDENCE, document_id, int(item.id))
    if key in pending:
        return None
    pending.add(key)
    return await propose(
        db,
        EVIDENCE,
        {
            "document_id": document_id,
            "request_item_id": int(item.id),
            "page": int(figure.payload["page"]),
            "because": figure.payload.get("source_note") or "",
        },
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _name_the_ask(
    db: AsyncSession,
    item: Any,
    pending: set[tuple[str, Any, Any]],
    owner_user_id: int | None,
) -> Any | None:
    """An ask nobody mapped, named by its own wording.

    Only when the wording names exactly one attribute. "Gross income
    and account balance" is two asks written as one, and picking either
    is the guess this pass refuses to make.
    """
    from app.services.finance.domains.writes.queue import propose

    key = (AMEND, int(item.id), None)
    if key in pending:
        return None
    named = _named_in(item.asked or "")
    if named is None:
        return None
    pending.add(key)
    attribute, label = named
    return await propose(
        db,
        AMEND,
        {
            "item_id": int(item.id),
            "ask": attribute,
            "reason": f'The ask says "{label}", and only a figure about '
            f"{label.lower()} can ever answer it.",
        },
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


def _named_in(asked: str) -> tuple[str, str] | None:
    """The one attribute this wording names, or nothing."""
    from app.services.matters.words import fact_attributes

    said = asked.casefold()
    found = [
        (key, label)
        for key, label in fact_attributes()
        if key != "other" and label.casefold() in said
    ]
    return found[0] if len(found) == 1 else None
