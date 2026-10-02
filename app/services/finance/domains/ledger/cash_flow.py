"""Money in against money out, read off the rows the Overview's income
vs spending bars count (``dated_amounts_in_window``), so every view of it
agrees: this month's spending against the usual month, day by day (#305),
and a range's totals and years for the Cash flow page (#344).
"""

from __future__ import annotations

from datetime import date
from itertools import accumulate
import statistics

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import queries, transactions
from app.services.finance.schemas import (
    CashflowMonth,
    CashFlowResponse,
    CashFlowYear,
    SpendingPace,
)
from app.services.finance.utils import (
    current_date,
    current_period_month,
    period_days,
    period_start,
    shift_period,
)


async def spending_pace(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    today: date,
    months: int = 12,
    account_ids: list[int] | None = None,
    rows: list[tuple[date, int]] | None = None,
) -> SpendingPace:
    """This month's cumulative spending through ``today``, and for every
    day of this month the average, the median and last month's cumulative
    spending over the ``months`` full months before it. A month shorter
    than this one holds its total to the end rather than dropping out."""
    current = current_period_month(today)
    earlier = [shift_period(current, -back) for back in range(months, 0, -1)]
    if rows is None:
        rows = await queries.dated_amounts_in_window(
            db,
            owner_user_id=owner_user_id,
            start=_pace_start(today, months),
            end=today,
            account_ids=account_ids,
        )
    daily: dict[int, list[int]] = {
        period: [0] * period_days(period)
        for period in [*earlier, current]
    }
    for day, amount in rows:
        if amount < 0 and (by_day := daily.get(current_period_month(day))) is not None:
            by_day[day.day - 1] -= amount
    running = {period: list(accumulate(by_day)) for period, by_day in daily.items()}
    days = len(daily[current])
    through = [
        [_through(running[p], day) for p in earlier] for day in range(1, days + 1)
    ]
    return SpendingPace(
        days=days,
        this_month=running[current][: today.day],
        average=[int(statistics.fmean(spent)) for spent in through],
        median=[int(statistics.median(spent)) for spent in through],
        last_month=[spent[-1] for spent in through],
    )


def _pace_start(today: date, months: int) -> date:
    return period_start(shift_period(current_period_month(today), -months))


async def overview_flows(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    months: int,
    end: date | None = None,
    account_ids: list[int] | None = None,
) -> tuple[list[CashflowMonth], SpendingPace]:
    """The Overview's monthly bars (``months`` ending with ``end``'s month,
    through ``end``) and its spending pace, from ONE read of the rows both
    count."""
    today = current_date()
    last = end or today
    first = shift_period(current_period_month(last), -(max(1, months) - 1))
    start = min(period_start(first), _pace_start(today, 12))
    rows = await queries.dated_amounts_in_window(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=max(last, today),
        account_ids=account_ids,
    )
    scope = {"owner_user_id": owner_user_id, "account_ids": account_ids, "rows": rows}
    return (
        await transactions.monthly_cashflow(db, months=months, today=last, **scope),
        await spending_pace(db, today=today, **scope),
    )


def _through(running: list[int], day: int) -> int:
    """A month's spending through ``day``: its total, past its own last day."""
    return running[min(day, len(running)) - 1]


async def cash_flow(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    start: date,
    end: date,
    account_ids: list[int] | None = None,
) -> CashFlowResponse:
    """Money in and money out from ``start`` through ``end``, and each
    calendar year's share of it, in one read."""
    rows = await queries.dated_amounts_in_window(
        db,
        owner_user_id=owner_user_id,
        start=start,
        end=end,
        account_ids=account_ids,
    )
    years: dict[int, list[int]] = {}
    for day, amount in rows:
        totals = years.setdefault(day.year, [0, 0])
        totals[0 if amount >= 0 else 1] += abs(amount)
    return CashFlowResponse(
        income=sum(income for income, _ in years.values()),
        spending=sum(spending for _, spending in years.values()),
        years=[
            CashFlowYear(year=year, income=income, spending=spending)
            for year, (income, spending) in sorted(years.items())
        ],
    )
