"""Reads against the budget tables, and the outflow fetches that feed them.

Statement builders only - no business logic, no writes. These are here
rather than in the planning package's shared ``queries`` because nothing
outside this package asks for them: a period's lines, a dismissal marker,
the tallied outflow rows a summary is built from.

``month_bounds`` sits with them on purpose: every one of these reads is
scoped by a YYYYMM period, and turning that period into a date range is
the first half of building the predicate.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import case, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import add_months
from app.services.finance.domains.planning.queries import (
    countable_filters,
    not_reconciliation,
    spend_filters,
    split_lines,
)
from app.services.finance.models import (
    FinanceBudget,
    FinanceBudgetCategory,
    FinanceTransaction,
    FinanceTransactionSplit,
)


def month_bounds(period_month: int) -> tuple[date, date]:
    """``[start, end)`` date range for a YYYYMM period."""
    year, month = divmod(period_month, 100)
    start = date(year, month, 1)
    return start, add_months(start, 1)


async def monthly_budget(
    db: AsyncSession, *, owner_user_id: int | None = None
) -> FinanceBudget | None:
    """The owner's standing "Monthly" budget row, if it exists."""
    query = select(FinanceBudget).where(
        FinanceBudget.name == "Monthly",
        FinanceBudget.deleted_at.is_(None),
    )
    if owner_user_id is not None:
        query = query.where(FinanceBudget.owner_user_id == owner_user_id)
    return (await db.exec(query.order_by(FinanceBudget.id))).first()


async def budget_lines_for_period(
    db: AsyncSession, budget_id: int, period_month: int
) -> list[FinanceBudgetCategory]:
    """Every row of the period, removed lines included: they say the
    month was decided. ``lines_in_force`` is the read that drops them."""
    return list(
        (
            await db.exec(
                select(FinanceBudgetCategory).where(
                    FinanceBudgetCategory.budget_id == budget_id,
                    FinanceBudgetCategory.period_month == period_month,
                )
            )
        ).all()
    )


async def budget_lines_before(
    db: AsyncSession, budget_id: int, before_period: int
) -> list[FinanceBudgetCategory]:
    """Every row of every month before ``before_period``, removed lines
    included, oldest first: the history a rolling-over limit carries
    from. Dismissal markers have no month and are not lines."""
    return list(
        (
            await db.exec(
                select(FinanceBudgetCategory)
                .where(
                    FinanceBudgetCategory.budget_id == budget_id,
                    FinanceBudgetCategory.period_month.is_not(None),
                    FinanceBudgetCategory.period_month < before_period,
                )
                .order_by(FinanceBudgetCategory.period_month)
            )
        ).all()
    )


async def latest_period_with_lines(
    db: AsyncSession, budget_id: int, before_period: int
) -> int | None:
    """The most recent period before ``before_period`` that has any lines.

    Not simply "last month": a month nobody opened should not break the
    chain, or a budget set in August would be lost by skipping September
    and looking at October.
    """
    return (
        await db.exec(
            select(FinanceBudgetCategory.period_month)
            .where(
                FinanceBudgetCategory.budget_id == budget_id,
                FinanceBudgetCategory.period_month < before_period,
            )
            .order_by(FinanceBudgetCategory.period_month.desc())
            .limit(1)
        )
    ).first()


async def budget_lines_with_category(
    db: AsyncSession, budget_id: int
) -> list[FinanceBudgetCategory]:
    """Category-carrying lines across ALL periods, dismissal markers
    included (period-less rows), and removed lines too: taking a limit off
    is a decision, not an invitation to suggest it again."""
    return list(
        (
            await db.exec(
                select(FinanceBudgetCategory).where(
                    FinanceBudgetCategory.budget_id == budget_id,
                    FinanceBudgetCategory.category_id.is_not(None),
                )
            )
        ).all()
    )


async def dismissal_marker_lines(
    db: AsyncSession, budget_id: int
) -> list[FinanceBudgetCategory]:
    """Period-less category rows - the "declined suggestion" markers."""
    return list(
        (
            await db.exec(
                select(FinanceBudgetCategory).where(
                    FinanceBudgetCategory.budget_id == budget_id,
                    FinanceBudgetCategory.category_id.is_not(None),
                    FinanceBudgetCategory.period_month.is_(None),
                )
            )
        ).all()
    )


async def budget_line_by_id(
    db: AsyncSession, line_id: int, *, owner_user_id: int | None = None
) -> FinanceBudgetCategory | None:
    filters = [
        FinanceBudgetCategory.id == line_id,
        FinanceBudgetCategory.deleted_at.is_(None),
    ]
    if owner_user_id is not None:
        filters.append(FinanceBudgetCategory.owner_user_id == owner_user_id)
    return (await db.exec(select(FinanceBudgetCategory).where(*filters))).first()


async def categorized_outflow_history(
    db: AsyncSession, *, owner_user_id: int | None, start: date, end: date
) -> list[FinanceTransaction]:
    """Countable, non-transfer, categorized outflows over ``[start, end)``
    - the corpus budget suggestions average over, under the same
    ``spend_filters`` as every other spend figure."""
    query = select(FinanceTransaction).where(
        *spend_filters(owner_user_id, start, end),
        FinanceTransaction.is_transfer.is_(False),
        FinanceTransaction.category_id.is_not(None),
    )
    return list((await db.exec(query)).all())


async def outflow_tuples(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    start: date,
    end: date | None = None,
    account_ids: list[int] | None = None,
    extra: tuple[Any, ...] = (),
) -> list[tuple]:
    """(category_id, merchant_name, original_description, name, amount,
    recurring_stream_id, *extra) for every countable outflow in the window
    - one fetch (plus one for split lines) a caller tallies by category /
    payee key / stream in a single Python pass. This is the query that
    keeps budget_summary O(1) in the number of lines and streams.
    ``extra`` columns of the transaction ride on the end, the date for a
    tally by month.

    Split-aware: a split parent is swapped for its lines, each carrying
    the parent's payee columns and stream - so category tallies see the
    lines while payee and stream tallies still add up to the parent."""
    filters = spend_filters(owner_user_id, start, end, account_ids)
    rows = (
        await db.exec(
            select(
                FinanceTransaction.category_id,
                FinanceTransaction.merchant_name,
                FinanceTransaction.original_description,
                FinanceTransaction.name,
                FinanceTransaction.amount,
                FinanceTransaction.recurring_stream_id,
                *extra,
            ).where(*filters, FinanceTransaction.is_split.is_(False))
        )
    ).all()
    split_rows = (
        await db.exec(
            split_lines(
                select(
                    FinanceTransactionSplit.category_id,
                    FinanceTransaction.merchant_name,
                    FinanceTransaction.original_description,
                    FinanceTransaction.name,
                    FinanceTransactionSplit.amount,
                    FinanceTransaction.recurring_stream_id,
                    *extra,
                )
            ).where(*filters)
        )
    ).all()
    return [*rows, *split_rows]


async def money_in_and_out(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    start: date,
    end: date,
    account_ids: list[int] | None = None,
) -> tuple[int, int]:
    """(cents in, cents out) over ``[start, end)``: countable rows, with
    money moved between your own accounts and reconciliations aside."""
    amount = FinanceTransaction.amount
    money_in, money_out = (
        await db.exec(
            select(
                func.coalesce(func.sum(case((amount > 0, amount), else_=0)), 0),
                func.coalesce(func.sum(case((amount < 0, -amount), else_=0)), 0),
            ).where(
                *countable_filters(owner_user_id, start, end, account_ids),
                FinanceTransaction.is_transfer.is_(False),
                not_reconciliation(),
            )
        )
    ).one()
    return int(money_in), int(money_out)


async def outflow_rows(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    start: date,
    end: date | None = None,
    account_ids: list[int] | None = None,
) -> list[FinanceTransaction]:
    """The countable outflows in the window as ROWS, under the very same
    ``spend_filters`` as ``outflow_tuples``.

    The tuples exist so a tally stays O(1) in the number of lines; this
    exists so a reader can see WHICH transactions made one line's total.
    Both sides have to agree, which is why the predicate is shared
    rather than restated - a drill-down that does not add up to the
    figure it was opened from is worse than no drill-down.

    Split parents come back whole: the caller decides whether a line
    matched the parent or one of its lines.
    """
    rows = (
        await db.exec(
            select(FinanceTransaction)
            .where(*spend_filters(owner_user_id, start, end, account_ids))
            .order_by(FinanceTransaction.date_.desc())
        )
    ).all()
    return list(rows)


async def category_dated_amounts_where(
    db: AsyncSession, filters: list
) -> list[tuple[int | None, date, int]]:
    """(category_id, date, amount) rows under caller-built predicates.

    Row-level rather than grouped: whether a category's spending recurs
    is a question about WHICH MONTHS it landed in, and grouping by month
    in SQL means date functions that differ per dialect.
    """
    rows = (
        await db.exec(
            select(
                FinanceTransaction.category_id,
                FinanceTransaction.date_,
                FinanceTransaction.amount,
            ).where(*filters)
        )
    ).all()
    return [(row[0], row[1], int(row[2])) for row in rows]


async def category_first_spend(
    db: AsyncSession, category_ids: set[int | None]
) -> dict[int, date]:
    """The first-ever outflow date per category - the length of its
    record. Deliberately unfiltered beyond "real spending": whether a
    given month's charge ended up covered by a bill or a budget later
    does not change when the category entered the user's life."""
    ids = {cid for cid in category_ids if cid is not None}
    if not ids:
        return {}
    rows = (
        await db.exec(
            select(
                FinanceTransaction.category_id,
                func.min(FinanceTransaction.date_),
            )
            .where(
                FinanceTransaction.category_id.in_(ids),  # type: ignore[union-attr]
                FinanceTransaction.deleted_at.is_(None),  # type: ignore[union-attr]
                FinanceTransaction.amount < 0,
                FinanceTransaction.is_transfer.is_(False),  # type: ignore[attr-defined]
            )
            .group_by(FinanceTransaction.category_id)
        )
    ).all()
    return {cid: seen for cid, seen in rows if cid is not None and seen is not None}
