"""A limit's months: each line against what it spent, months back (#345).

The budget page reads one month at a time; this reads a line across the
last 3, 6 or 12 months that have ended, with the limit each month ran on
and the average.
"""

from datetime import date
from typing import Any

import pytest

from app.services.finance.service import FinanceService
from tests.services._finance_factories import (
    category_id,
    seed_limit,
    seed_monthly_spend,
)

# Each limit a test seeds per month answers with its own read-back.
pytestmark = pytest.mark.queryspy(threshold=5)

JULY, AUGUST, SEPTEMBER, OCTOBER = 202607, 202608, 202609, 202610
TODAY = date(2026, 10, 15)


async def _history(svc: FinanceService, months: int = 3) -> dict[int, Any]:
    """October's lines' histories, by line id."""
    history = await svc.budget_history(
        owner_user_id=1, months=months, period_month=OCTOBER
    )
    return {item.line_id: item for item in history.items}


class TestALinesMonths:
    @pytest.mark.asyncio
    async def test_each_month_reads_against_the_limit_it_ran_on(
        self, svc: FinanceService
    ) -> None:
        """$200 in July and August, raised to $250 in September; spent
        $150, $230 and $240. Oldest first, the average over three."""
        groceries = await category_id(svc, "Food:Groceries")
        await seed_limit(svc, groceries, 20_000, period_month=JULY)
        await seed_limit(svc, groceries, 25_000, period_month=SEPTEMBER)
        line = await seed_limit(svc, groceries, 25_000, period_month=OCTOBER)
        await seed_monthly_spend(
            svc, groceries, {JULY: 15_000, AUGUST: 23_000, SEPTEMBER: 24_000}
        )

        item = (await _history(svc))[line.id]

        assert [
            (m.period_month, m.allocated_amount, m.spent_amount) for m in item.months
        ] == [
            (JULY, 20_000, 15_000),
            (AUGUST, 20_000, 23_000),  # nobody opened August: July's plan ran
            (SEPTEMBER, 25_000, 24_000),
        ]
        assert item.average_amount == 20_666
        assert item.over_count == 1

    @pytest.mark.asyncio
    async def test_a_month_without_the_limit_has_spend_but_no_limit(
        self, svc: FinanceService
    ) -> None:
        """Dining had no limit in July: its spend still counts toward the
        average, and a month with no limit cannot be over it."""
        groceries = await category_id(svc, "Food:Groceries")
        dining = await category_id(svc, "Food:Restaurants")
        await seed_limit(svc, groceries, 20_000, period_month=JULY)
        await seed_limit(svc, dining, 10_000, period_month=AUGUST)
        await seed_limit(svc, groceries, 20_000, period_month=OCTOBER)
        line = await seed_limit(svc, dining, 10_000, period_month=OCTOBER)
        await seed_monthly_spend(svc, dining, {JULY: 30_000, AUGUST: 6_000})

        item = (await _history(svc))[line.id]

        assert [(m.allocated_amount, m.spent_amount) for m in item.months] == [
            (None, 30_000),
            (10_000, 6_000),
            (10_000, 0),
        ]
        assert (item.average_amount, item.over_count) == (12_000, 0)

    @pytest.mark.asyncio
    async def test_the_window_is_the_months_that_have_ended(
        self, svc: FinanceService
    ) -> None:
        groceries = await category_id(svc, "Food:Groceries")
        line = await seed_limit(svc, groceries, 20_000, period_month=OCTOBER)
        await seed_monthly_spend(svc, groceries, {OCTOBER: 5_000})

        item = (await _history(svc, months=6))[line.id]

        assert [m.period_month for m in item.months] == [
            202604,
            202605,
            202606,
            JULY,
            AUGUST,
            SEPTEMBER,
        ]
        assert sum(m.spent_amount for m in item.months) == 0  # October is not in it

    @pytest.mark.asyncio
    async def test_no_limits_no_history(self, svc: FinanceService) -> None:
        assert await _history(svc) == {}
