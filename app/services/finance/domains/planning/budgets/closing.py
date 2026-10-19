"""A month that is over stays over (#319).

A past month's bills, income and everything-else were rebuilt from
today's bills and a window ending today, so a year-old month changed by
itself. Now each month is recorded when it ends - the summary the page
showed and what came in and went out - and read back from the record.
A charge filed into it later does not rewrite it: the page says what
changed since, beside what was recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.planning.budgets import queries
from app.services.finance.domains.planning.budgets.summary import (
    budget_summary,
    month_actuals,
)
from app.services.finance.models import FinanceBudgetMonth
from app.services.finance.schemas import BudgetMonthActuals, BudgetSummaryResponse
from app.services.shared.queries import stored_owner


async def close_month(
    db: AsyncSession, *, owner_user_id: int | None, period_month: int
) -> FinanceBudgetMonth:
    """The month's record, written the first time it is asked for: as of
    its own last day, so its bills and income are that month's."""
    found = await queries.closed_month(
        db, owner_user_id=stored_owner(owner_user_id), period_month=period_month
    )
    return found or await _record(db, owner_user_id, period_month)


async def _record(
    db: AsyncSession, owner_user_id: int | None, period_month: int
) -> FinanceBudgetMonth:
    _start, end = queries.month_bounds(period_month)
    summary = await budget_summary(
        db,
        owner_user_id=owner_user_id,
        period_month=period_month,
        today=end - timedelta(days=1),
    )
    # Read as of its last day, which still has that day left; recorded,
    # the month is over.
    summary.stats.days_left_in_period = 0
    actuals = await month_actuals(
        db, owner_user_id=owner_user_id, period_month=period_month
    )
    record = FinanceBudgetMonth(
        owner_user_id=stored_owner(owner_user_id),
        period_month=period_month,
        summary=summary.model_dump(mode="json"),
        actuals=actuals.model_dump(mode="json"),
    )
    db.add(record)
    await db.flush()
    return record


@dataclass(frozen=True)
class ClosedMonth:
    """An ended month as recorded, and what was filed into it since."""

    summary: BudgetSummaryResponse
    actuals: BudgetMonthActuals
    # What it reads now, when that differs from the record; else None.
    now: BudgetMonthActuals | None

    def changed_since(self) -> dict[str, int] | None:
        """Money in and out as recorded and as now, when filed into since."""
        if self.now is None:
            return None
        return {
            "money_in_then": self.actuals.money_in,
            "money_in_now": self.now.money_in,
            "money_out_then": self.actuals.money_out,
            "money_out_now": self.now.money_out,
        }


async def month_as_closed(
    db: AsyncSession, *, owner_user_id: int | None, period_month: int
) -> ClosedMonth | None:
    """An ended month as it was recorded when it ended, or None when it
    never was (it ended before records were kept): only the month-end job
    records one, never a reading, which would record today's bills."""
    record = await queries.closed_month(
        db, owner_user_id=stored_owner(owner_user_id), period_month=period_month
    )
    if record is None:
        return None
    was = BudgetMonthActuals.model_validate(record.actuals)
    now = await month_actuals(
        db, owner_user_id=owner_user_id, period_month=period_month
    )
    changed = (now.money_in, now.money_out) != (was.money_in, was.money_out)
    return ClosedMonth(
        summary=BudgetSummaryResponse.model_validate(record.summary),
        actuals=was,
        now=now if changed else None,
    )
