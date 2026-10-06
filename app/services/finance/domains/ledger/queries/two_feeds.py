"""The rows still waiting for their other feed (``domains/ledger/two_feeds``)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from typing import NamedTuple

from sqlalchemy import ColumnElement
from sqlmodel import and_, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger.queries.filters import not_duplicate
from app.services.finance.models import FinanceTransaction


class FeedRow(NamedTuple):
    """A row waiting for its other feed, as the pairing reads it."""

    id: int
    account_id: int
    date_: date
    amount: int
    source: str
    # The detected stream it belongs to: a repeating charge says less
    # about which account it is than a one-off (``placing.suggestions``).
    recurring_stream_id: int | None = None


def pairable(sources: Collection[str]) -> ColumnElement[bool]:
    """A row that can still pair with the other feed's: from ``sources``,
    posted, and not paired yet. The one statement of it - the sync reads
    it as a filter (``unpaired_rows``), an import as a column of the lane
    rows it already loads (``importers.base.LaneRow``)."""
    return and_(
        col(FinanceTransaction.source).in_(sources),
        FinanceTransaction.pending.is_(False),
        FinanceTransaction.dedup_status == "unique",
    )


async def unpaired_rows(
    db: AsyncSession,
    account_ids: Collection[int],
    sources: Collection[str],
    start: date,
    end: date,
) -> list[FeedRow]:
    """Live rows from ``sources`` on these accounts still able to pair,
    dated ``start``..``end``: four columns, never the entity. Bounded by
    the incoming dates, not the ledger - years of history are not read to
    pair one month."""
    if not account_ids:
        return []
    rows = await db.exec(
        select(
            FinanceTransaction.id,
            FinanceTransaction.account_id,
            FinanceTransaction.date_,
            FinanceTransaction.amount,
            FinanceTransaction.source,
        ).where(
            col(FinanceTransaction.account_id).in_(account_ids),
            col(FinanceTransaction.deleted_at).is_(None),
            pairable(sources),
            FinanceTransaction.date_ >= start,
            FinanceTransaction.date_ <= end,
        )
    )
    return [FeedRow(*row) for row in rows.all()]


async def rows_between(
    db: AsyncSession, account_ids: Collection[int], start: date, end: date
) -> list[FeedRow]:
    """Every live, posted row on these accounts dated ``start``..``end``,
    whichever feed brought it, as the pairing reads rows: what a held
    account's charges are compared with to suggest where it goes."""
    if not account_ids:
        return []
    rows = await db.exec(
        select(
            FinanceTransaction.id,
            FinanceTransaction.account_id,
            FinanceTransaction.date_,
            FinanceTransaction.amount,
            FinanceTransaction.source,
            FinanceTransaction.recurring_stream_id,
        ).where(
            col(FinanceTransaction.account_id).in_(account_ids),
            col(FinanceTransaction.deleted_at).is_(None),
            FinanceTransaction.pending.is_(False),
            not_duplicate(),
            FinanceTransaction.date_ >= start,
            FinanceTransaction.date_ <= end,
        )
    )
    return [FeedRow(*row) for row in rows.all()]
