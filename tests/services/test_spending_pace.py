"""Spending this month against the average month, day by day (#305).

Cumulative spending by day of the month: this month so far, and at each
day the average (and the median, and last month) of the earlier months'
spending up to that same day. Spending is what the Overview's income-vs-
spending bars count, so the line and the bars agree.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import cash_flow
from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account, seed_txn

TODAY = date(2026, 10, 15)


async def _spend(svc: FinanceService, *charges: tuple[date, int]) -> None:
    account = await seed_account(svc)
    for day, cents in charges:
        await seed_txn(svc, account.id, -cents, day)


class TestThePace:
    @pytest.mark.asyncio
    async def test_this_month_against_the_same_day_of_earlier_months(
        self, svc: FinanceService
    ) -> None:
        await _spend(
            svc,
            (date(2026, 7, 10), 10_000),
            (date(2026, 8, 10), 20_000),
            (date(2026, 8, 20), 10_000),
            (date(2026, 9, 10), 30_000),
            (date(2026, 10, 5), 5_000),
            (date(2026, 10, 12), 10_000),
        )

        pace = await svc.spending_pace(owner_user_id=1, months=3, today=TODAY)

        assert pace.days == 31
        assert len(pace.this_month) == 15  # through today, not past it
        assert (pace.this_month[3], pace.this_month[4], pace.this_month[14]) == (
            0,
            5_000,
            15_000,
        )
        assert pace.average[9] == 20_000  # day 10: 100, 200 and 300
        assert pace.average[19] == 23_333  # day 20: August's second charge
        assert pace.median[19] == 30_000
        assert pace.last_month[9] == 30_000

    @pytest.mark.asyncio
    async def test_a_shorter_month_holds_its_total_to_the_end(
        self, svc: FinanceService
    ) -> None:
        """September has 30 days: on October's 31st it still counts, at its
        month's total, rather than dropping out of the average."""
        await _spend(svc, (date(2026, 9, 30), 6_000))

        pace = await svc.spending_pace(owner_user_id=1, months=1, today=TODAY)

        assert (pace.average[29], pace.average[30]) == (6_000, 6_000)

    @pytest.mark.asyncio
    async def test_a_bill_on_the_first_is_a_step_in_the_average(
        self, svc: FinanceService
    ) -> None:
        """Rent on the 1st every month: day one of this month is level with
        the average, not "over" it."""
        await _spend(
            svc,
            (date(2026, 8, 1), 250_000),
            (date(2026, 9, 1), 250_000),
            (date(2026, 10, 1), 250_000),
        )

        pace = await svc.spending_pace(owner_user_id=1, months=2, today=TODAY)

        assert pace.this_month[0] == pace.average[0] == 250_000

    @pytest.mark.asyncio
    async def test_money_in_is_not_spending(self, svc: FinanceService) -> None:
        account = await seed_account(svc)
        await seed_txn(svc, account.id, 500_000, date(2026, 10, 2))

        pace = await svc.spending_pace(owner_user_id=1, months=1, today=TODAY)

        assert pace.this_month[-1] == 0


class TestMonthToDate:
    """#294: a partial month beside last month to the same day."""

    @pytest.mark.asyncio
    async def test_last_month_counts_only_to_the_same_day(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await seed_account(svc)
        await seed_txn(svc, account.id, 300_000, date(2026, 9, 1))  # paid
        await seed_txn(svc, account.id, -20_000, date(2026, 9, 10))
        await seed_txn(svc, account.id, -90_000, date(2026, 9, 25))  # after the 15th
        await seed_txn(svc, account.id, 300_000, date(2026, 10, 1))
        await seed_txn(svc, account.id, -25_000, date(2026, 10, 12))

        mtd = await cash_flow.month_to_date(
            async_db_session, owner_user_id=1, today=TODAY
        )

        assert mtd.through_day == 15
        assert (mtd.this_month.income, mtd.this_month.spending) == (300_000, 25_000)
        assert (mtd.last_month_same_days.income, mtd.last_month_same_days.spending) == (
            300_000,
            20_000,
        )

    @pytest.mark.asyncio
    async def test_the_31st_meets_a_shorter_month_at_its_end(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await seed_account(svc)
        await seed_txn(svc, account.id, -1_000, date(2026, 9, 30))

        mtd = await cash_flow.month_to_date(
            async_db_session, owner_user_id=1, today=date(2026, 10, 31)
        )

        assert mtd.last_month_same_days.spending == 1_000
