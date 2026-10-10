"""A receipt that arrives before its charge waits for it (#330).

A receipt on the shelf that is on no transaction is WAITING: its total
and date are read off its pages, and each sync, import and nightly sweep
looks for the one charge it is - the total, posted near the receipt's
date. A match is a ``receipt.attach`` card; approving files the receipt
on the charge, the same tag the register's paperclip files by. After
``WAIT_DAYS`` on the shelf it stops waiting and becomes an Attention
item, so a receipt nothing will ever match does not sit there in
silence.

No new state: waiting is derived (a live receipt, no transaction tag,
young enough), and what was already asked is read off the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.figures import MONEY
from app.services.documents.domains.reading.findings import Page
from app.services.finance.schemas import ChangeDisplayRow

RECEIPT = "receipt.attach"
UNMATCHED = "receipt_unmatched"
PROPOSED_BY = "reading"
# How long a receipt waits for its charge before it asks for a person,
# and how long after that the nightly sweep has to ask: a receipt older
# than both - one kept on the shelf before receipts waited - is not
# waiting for anything, and is never read again.
WAIT_DAYS = 30
_ASKING_DAYS = 7
# Where its charge posts, against the date on the receipt: a hold can
# post the day before, an order is charged when it ships.
_POSTS = (timedelta(days=-3), timedelta(days=14))
# A receipt with no date of its own is matched around its arrival.
_UNDATED = (timedelta(days=-30), timedelta(days=14))

# The line that says what was paid. Not the subtotal, the tax or what
# was saved: "Total savings $4.00" is not what the card was charged.
_TOTAL = re.compile(
    r"\b(grand total|order total|total charged|total paid|amount charged"
    r"|amount paid|total)\b",
    re.I,
)
_NOT_TOTAL = re.compile(
    r"\b(sub\s*-?\s*total|tax|savings|saved|discount|before|items?)\b", re.I
)


@dataclass(frozen=True)
class Total:
    """What a receipt says was paid, and the line it was read off."""

    cents: int
    page: int
    because: str


def read_total(pages: list[Page]) -> Total | None:
    """The receipt's total: the largest figure on a line that labels a
    total. Largest, because "Total before tax" and "Order total" can
    both print, and what the card is charged is the larger."""
    from app.services.finance.utils import money_to_cents

    found: list[Total] = []
    for page in pages:
        for line in (page["text"] or "").splitlines():
            said = line.strip()
            label = _TOTAL.search(said)
            if label is None or _NOT_TOTAL.search(said):
                continue
            money = MONEY.findall(said[label.end() :])
            cents = money_to_cents(money[-1].replace(" ", "")) if money else None
            if cents:
                found.append(Total(abs(cents), page["page"], said))
    return max(found, key=lambda t: t.cents, default=None)


def _printed_date(pages: list[Page]) -> date | None:
    """The first date a receipt prints - when it was bought, on every
    receipt seen - where the reader found no dateline ("Order placed
    September 3, 2026" is not one)."""
    from app.services.documents.domains.reading.patterns import find_date

    for page in pages:
        for line in (page["text"] or "").splitlines():
            if found := find_date(line):
                return found[0]
    return None


class ReceiptPayload(BaseModel):
    """This receipt is that charge's: the total it was matched by, and
    the line it was read off."""

    model_config = ConfigDict(extra="forbid")

    document_id: int
    # The receipt's name when it was matched, so a batch of these draws
    # without reading each document back.
    title: str
    transaction_id: int
    total_cents: int
    page: int
    because: str


async def receipt_execute(
    db: AsyncSession, payload: ReceiptPayload, owner_user_id: int | None
) -> dict[str, Any]:
    """File the receipt on its charge, as the paperclip does."""
    from app.services.documents.service import DocumentService
    from app.services.finance.constants import transaction_tag

    if (
        await DocumentService(db).tag(
            payload.document_id, transaction_tag(payload.transaction_id)
        )
        is None
    ):
        raise ValueError(f"Document {payload.document_id} is gone.")
    return {
        "transaction_id": payload.transaction_id,
        "document_id": payload.document_id,
    }


async def receipt_describe(
    db: AsyncSession, payload: ReceiptPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.core.formatting import format_money
    from app.services.finance.domains.writes.display import txn_row

    _txn, subject = await txn_row(db, payload.transaction_id, owner_user_id)
    return [
        subject,
        ChangeDisplayRow(
            label="Receipt",
            value=f"{payload.title}, {format_money(payload.total_cents)}",
            note=f"page {payload.page}: {payload.because}",
            document_id=payload.document_id,
            page=payload.page,
        ),
    ]


async def match_waiting(
    db: AsyncSession, *, owner_user_id: int | None, today: date | None = None
) -> int:
    """Look again for every waiting receipt's charge; propose the ones
    found as one batch, and put the ones done waiting in front of a
    person. Returns how many cards were made."""
    from app.services.finance.domains.writes.queue import propose_many
    from app.services.finance.utils import current_date

    today = today or current_date()
    since = today - timedelta(days=WAIT_DAYS + _ASKING_DAYS)
    waiting = await _waiting(db, owner_user_id, since)
    if not waiting:
        return 0
    fresh = [w for w in waiting if (today - w.arrived).days <= WAIT_DAYS]
    await _done_waiting(db, [w for w in waiting if w not in fresh], owner_user_id)
    asked, refused = await _asked(db)
    fresh = [w for w in fresh if w.document_id not in asked]
    charges = await _charges(db, fresh, owner_user_id)
    cards: list[dict[str, Any]] = []
    claimed: set[int] = set()
    for receipt in fresh:
        start, end = receipt.window()
        found = [
            c
            for c in charges
            if -c.amount == receipt.total.cents
            and start <= c.date_ <= end
            and c.id not in claimed
            and (receipt.document_id, c.id) not in refused
        ]
        # ponytail: two charges of the same total in the window is no
        # answer, and the receipt keeps waiting; a payee tie-break would
        # settle the rest if it comes up.
        if len(found) != 1:
            continue
        claimed.add(int(found[0].id or 0))
        cards.append(
            {
                "document_id": receipt.document_id,
                "title": receipt.title,
                "transaction_id": found[0].id,
                "total_cents": receipt.total.cents,
                "page": receipt.total.page,
                "because": receipt.total.because,
            }
        )
    if cards:
        await propose_many(
            db,
            RECEIPT,
            cards,
            owner_user_id=owner_user_id,
            proposed_by_agent=PROPOSED_BY,
        )
    return len(cards)


@dataclass(frozen=True)
class _Waiting:
    document_id: int
    title: str
    total: Total
    dated: date | None
    arrived: date

    def window(self) -> tuple[date, date]:
        around, (before, after) = (
            (self.dated, _POSTS) if self.dated else (self.arrived, _UNDATED)
        )
        return around + before, around + after


async def _waiting(
    db: AsyncSession, owner_user_id: int | None, since: date
) -> list[_Waiting]:
    """Live receipts on no transaction that arrived since ``since``, with
    a total that can be read: one read for the documents, one for their
    pages."""
    from app.services.documents.models import Document, DocumentPage, DocumentTag
    from app.services.finance.constants import transaction_tag

    filed = select(DocumentTag.document_id).where(
        col(DocumentTag.label).startswith(transaction_tag(0)[:-1])
    )
    query = select(Document).where(
        Document.kind == "receipt",
        col(Document.deleted_at).is_(None),
        col(Document.id).not_in(filed),
        col(Document.created_at) >= datetime.combine(since, time.min),
    )
    if owner_user_id is not None:
        query = query.where(Document.owner_user_id == owner_user_id)
    documents = list((await db.exec(query)).all())
    if not documents:
        return []
    pages: dict[int, list[Page]] = {}
    for row in (
        await db.exec(
            select(DocumentPage)
            .where(col(DocumentPage.document_id).in_([d.id for d in documents]))
            .order_by(col(DocumentPage.page_number))
        )
    ).all():
        pages.setdefault(row.document_id, []).append(
            {"page": row.page_number, "text": row.text}
        )
    return [
        _Waiting(
            int(d.id or 0),
            d.title,
            total,
            d.document_date or _printed_date(pages.get(int(d.id or 0), [])),
            (d.received_at or d.created_at).date(),
        )
        for d in documents
        if (total := read_total(pages.get(int(d.id or 0), [])))
    ]


async def _asked(db: AsyncSession) -> tuple[set[int], set[tuple[int, int]]]:
    """Receipts with a card still pending, and the matches already
    turned down - neither is asked again."""
    from app.services.finance.models import FinancePendingChange as C

    rows = (
        await db.exec(
            select(C).where(
                C.change_type == RECEIPT, col(C.status).in_(("pending", "rejected"))
            )
        )
    ).all()
    return (
        {int(r.payload["document_id"]) for r in rows if r.status == "pending"},
        {
            (int(r.payload["document_id"]), int(r.payload["transaction_id"]))
            for r in rows
            if r.status == "rejected"
        },
    )


async def _charges(
    db: AsyncSession, waiting: list[_Waiting], owner_user_id: int | None
) -> list[Any]:
    """Every charge one of these receipts could be, in one read: of one
    of their totals, inside the widest window, wearing no receipt."""
    from app.services.documents.queries import tagged_with
    from app.services.finance.constants import transaction_tag
    from app.services.finance.domains.ledger.queries.filters import not_duplicate
    from app.services.finance.models import FinanceTransaction as T

    if not waiting:
        return []
    windows = [w.window() for w in waiting]
    query = select(T).where(
        col(T.amount).in_({-w.total.cents for w in waiting}),
        col(T.date_) >= min(s for s, _ in windows),
        col(T.date_) <= max(e for _, e in windows),
        col(T.deleted_at).is_(None),
        not_duplicate(),
    )
    if owner_user_id is not None:
        query = query.where(T.owner_user_id == owner_user_id)
    charges = list((await db.exec(query)).all())
    receipted = await tagged_with(
        db, [transaction_tag(int(c.id or 0)) for c in charges]
    )
    return [c for c in charges if not receipted.get(transaction_tag(int(c.id or 0)))]


async def _done_waiting(
    db: AsyncSession, done: list[_Waiting], owner_user_id: int | None
) -> None:
    """A receipt nothing matched in ``WAIT_DAYS``: an Attention item,
    once, saying what it is and what to do with it."""
    from app.core.formatting import format_money
    from app.services.finance.domains.detection.insights.rules.shared import (
        create_insight_if_new,
    )
    from app.services.finance.models import FinanceInsight
    from app.services.shared.queries import stored_owner

    if not done:
        return
    keys = {f"{UNMATCHED}:{r.document_id}" for r in done}
    flagged = set(
        (
            await db.exec(
                select(FinanceInsight.dedup_key).where(
                    col(FinanceInsight.dedup_key).in_(keys)
                )
            )
        ).all()
    )
    for receipt in done:
        if f"{UNMATCHED}:{receipt.document_id}" in flagged:
            continue
        on = f" from {receipt.dated:%b %-d}" if receipt.dated else ""
        await create_insight_if_new(
            db,
            owner_user_id=stored_owner(owner_user_id),
            insight_type=UNMATCHED,
            dedup_key=f"{UNMATCHED}:{receipt.document_id}",
            severity="info",
            title=f"No charge found for {receipt.title}",
            body=f"{format_money(receipt.total.cents)}{on}, waited {WAIT_DAYS} days. "
            "Attach it from its transaction's menu, or dismiss this.",
            detected_amount=receipt.total.cents,
        )
