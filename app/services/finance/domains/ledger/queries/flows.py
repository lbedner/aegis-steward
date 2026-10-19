"""Money in and money out: the rows every income and spending figure is
tallied from, and the one tally (#316).

``dated_amounts_in_window`` hands each row with what it counts as
(``money_kind``); ``money_flow`` is the one place that kind becomes a
figure. The payee and per-account roll-ups read by the same rule.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import case, func, union_all
from sqlmodel import and_, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger.queries.filters import (
    counts_as,
    household_account_ids,
    money_kind,
    not_duplicate,
)
from app.services.finance.models import (
    FinanceMerchant,
    FinanceTransaction,
    FinanceTransactionSplit,
)
from app.services.finance.utils import current_date
from app.services.shared.queries import owner_filters

# A ``dated_amounts_in_window`` row: (date, signed amount, kind).
FlowRow = tuple[date, int, str]


def money_flow(rows: Iterable[FlowRow]) -> dict[str, int]:
    """Money in, money out and the credits nobody filed, all positive: the
    one place a row's kind becomes a figure (#316). A return nets against
    spending and a clawback against income; an unfiled credit is neither,
    and counted apart so a page can say so."""
    flow = {"income": 0, "spending": 0, "unfiled": 0}
    for _day, amount, kind in rows:
        if kind == "income":
            flow["income"] += amount
        elif kind == "spending":
            flow["spending"] -= amount
        elif kind == "unfiled":
            flow["unfiled"] += amount
    return flow


async def dated_amounts_in_window(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    start: date,
    end: date,
    account_ids: list[int] | None = None,
) -> list[tuple[date, int, str]]:
    """(date, amount, kind) for cashflow bucketing: live accounts,
    non-duplicate, report-included, transfers excluded. ``kind`` is what
    the row counts as (``money_kind``, #316), and a split parent reads as
    its lines, each counted by what it is filed under."""
    filters = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.excluded_from_reports.is_(False),
        FinanceTransaction.is_transfer.is_(False),
        FinanceTransaction.account_id.in_(household_account_ids()),
        FinanceTransaction.date_ >= start,
        FinanceTransaction.date_ <= end,
    ]
    if account_ids is not None:
        filters.append(FinanceTransaction.account_id.in_(account_ids))
    filters.extend(owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    txn, line = FinanceTransaction, FinanceTransactionSplit
    rows = select(txn.date_, txn.amount, money_kind(txn.category_id, txn.amount)).where(
        *filters, txn.is_split.is_(False)
    )
    lines = (
        select(txn.date_, line.amount, money_kind(line.category_id, line.amount))
        .join(txn, txn.id == line.parent_transaction_id)
        .where(*filters, txn.is_split.is_(True))
    )
    found = (await db.exec(union_all(rows, lines))).all()
    return [(day, amount, kind) for day, amount, kind in found]


async def counted_as(db: AsyncSession, ids: list[int]) -> dict[int, str]:
    """What each row counts as in every income and spending figure: its
    ``money_kind`` (#316); "split" when each of its lines counts by its
    own filing; "not counted" when it is a transfer, left out of reports
    or someone else's money (#314)."""
    if not ids:
        return {}
    txn = FinanceTransaction
    kind = case(
        (
            or_(
                txn.is_transfer.is_(True),
                txn.excluded_from_reports.is_(True),
                txn.account_id.notin_(household_account_ids()),
            ),
            "not counted",
        ),
        (txn.is_split.is_(True), "split"),
        else_=money_kind(txn.category_id, txn.amount),
    )
    rows = (await db.exec(select(txn.id, kind).where(txn.id.in_(ids)))).all()
    return {int(row_id): str(found) for row_id, found in rows}


async def spent_by_category_rows(
    db: AsyncSession,
    filters: list[Any],
    category: Callable[[Any], Any] | None = None,
) -> list[tuple[int | None, date, int]]:
    """(category id, date, signed amount) of every row that counts as
    spending under ``filters`` (on the transaction), in one statement. A
    split parent reads as its lines, each by its own category and kind
    (#316): a loan payment's principal line is not spending however the
    payment is filed. ``category`` narrows by a category column - the
    row's, or the line's."""
    txn, line = FinanceTransaction, FinanceTransactionSplit

    def counted(column: Any, amount: Any) -> list[Any]:
        return [
            money_kind(column, amount) == "spending",
            *([category(column)] if category is not None else []),
        ]

    rows = select(txn.category_id, txn.date_, txn.amount).where(
        *filters, txn.is_split.is_(False), *counted(txn.category_id, txn.amount)
    )
    lines = (
        select(line.category_id, txn.date_, line.amount)
        .join(txn, txn.id == line.parent_transaction_id)
        .where(
            *filters, txn.is_split.is_(True), *counted(line.category_id, line.amount)
        )
    )
    found = (await db.exec(union_all(rows, lines))).all()
    return [(category_id, day, int(amount)) for category_id, day, amount in found]


async def top_payees_over_window(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    days: int = 90,
    limit: int = 8,
) -> list[tuple[str, int, int]]:
    """(payee, outflow total as positive cents, transaction count) grouped
    by payee, biggest first, transfers excluded.

    The payee is ``payee_label``'s rule in SQL: the payee somebody NAMED
    first, then the one the source supplied, then the raw descriptor.
    Grouping on the source's name alone made this list disagree with
    every other surface - the register showed "Maple Nursing Care"
    while this showed "Recurring Withdrawal Debit Card CK *Maple Nu...".

    It is not only the label. Naming a payee is what makes its rows ADD
    UP: on the live ledger one Shop Rite reached this list at $662.78
    across seven transactions that had been seven separate descriptors,
    and two American Express charges that had been filed under two
    different interest-charge descriptions became one row. A list of
    biggest payees built on descriptors ranks the wrong things.
    """
    payee = func.coalesce(
        FinanceMerchant.name,
        FinanceTransaction.merchant_name,
        FinanceTransaction.name,
    )
    filters = [
        FinanceTransaction.deleted_at.is_(None),
        not_duplicate(),
        FinanceTransaction.excluded_from_reports.is_(False),
        FinanceTransaction.is_transfer.is_(False),
        FinanceTransaction.account_id.in_(household_account_ids()),
        FinanceTransaction.date_ >= current_date() - timedelta(days=days),
        payee.is_not(None),
    ]
    filters.extend(owner_filters(FinanceTransaction.owner_user_id, owner_user_id))
    txn, line = FinanceTransaction, FinanceTransactionSplit
    # Spending net of its returns (#316) - a refunded order is not a top
    # payee - and a split payment by its spending lines (a loan's
    # principal is not what was paid to it).
    spent = union_all(
        select(payee.label("payee"), txn.amount.label("amount"), txn.id.label("id"))
        .select_from(txn)
        .outerjoin(FinanceMerchant, FinanceMerchant.id == txn.merchant_id)
        .where(*filters, txn.is_split.is_(False), counts_as("spending")),
        select(payee.label("payee"), line.amount.label("amount"), txn.id.label("id"))
        .select_from(line)
        .join(txn, txn.id == line.parent_transaction_id)
        .outerjoin(FinanceMerchant, FinanceMerchant.id == txn.merchant_id)
        .where(*filters, txn.is_split.is_(True), counts_as("spending", line=True)),
    ).subquery()
    total = func.sum(spent.c.amount)
    rows = (
        await db.exec(
            select(spent.c.payee, total, func.count(func.distinct(spent.c.id)))
            .group_by(spent.c.payee)
            .having(total < 0)
            .order_by(total)
            .limit(limit)
        )
    ).all()
    return [(name, -int(total or 0), int(count or 0)) for name, total, count in rows]


async def outflow_by_account_in_window(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    start: date,
    end: date,
    exclude_stream_ids: Sequence[int] | None = None,
    include_transfer_stream_ids: Sequence[int] | None = None,
) -> list[tuple[int, int]]:
    """(account_id, money out as positive cents) over the window.

    What actually left, rather than what was declared: the measured
    denominator a fund sized in months of expenses needs. Transfers are
    excluded by the same filter every other spending read uses, which is
    also what keeps a card payment from counting on top of the swipes it
    settles - but ``is_transfer`` is set by matching, so a payment nobody
    matched still looks like spending. ``exclude_stream_ids`` drops whole
    streams for that case: the stream knows it is a card payment even
    when one of its transactions was never paired.

    ``include_transfer_stream_ids`` is the opposite door, for transfers
    that ARE expenses. A matched mortgage payment is a transfer into a
    liability, so the blanket transfer filter would drop it - and unlike
    a card payment, nothing else in the ledger records that money
    leaving.
    """
    txn, line = FinanceTransaction, FinanceTransactionSplit
    include = list(include_transfer_stream_ids or [])

    def spending(column: Any, amount: Any) -> Any:
        """Spending, net of its returns (#316), judged line by line for a
        split payment: a loan's principal line is not spending."""
        spent = and_(
            txn.excluded_from_reports.is_(False),
            txn.is_transfer.is_(False),
            money_kind(column, amount) == "spending",
        )
        if not include:
            return spent
        # Pairing leaves a payment out of reports as well as a transfer:
        # both flags are what the door has to open past - and every line
        # of it, principal included, is money that left.
        return or_(spent, and_(amount < 0, txn.recurring_stream_id.in_(include)))

    filters = [
        txn.deleted_at.is_(None),
        not_duplicate(),
        txn.account_id.in_(household_account_ids()),
        txn.date_ >= start,
        txn.date_ <= end,
    ]
    filters.extend(owner_filters(txn.owner_user_id, owner_user_id))
    if exclude_stream_ids:
        filters.append(
            or_(
                txn.recurring_stream_id.is_(None),
                txn.recurring_stream_id.notin_(list(exclude_stream_ids)),
            )
        )
    out = union_all(
        select(txn.account_id.label("account_id"), txn.amount.label("amount")).where(
            *filters, txn.is_split.is_(False), spending(txn.category_id, txn.amount)
        ),
        select(txn.account_id.label("account_id"), line.amount.label("amount"))
        .join(txn, txn.id == line.parent_transaction_id)
        .where(
            *filters, txn.is_split.is_(True), spending(line.category_id, line.amount)
        ),
    ).subquery()
    rows = (
        await db.exec(
            select(out.c.account_id, func.sum(out.c.amount)).group_by(out.c.account_id)
        )
    ).all()
    return [(int(account_id), -int(total or 0)) for account_id, total in rows]
