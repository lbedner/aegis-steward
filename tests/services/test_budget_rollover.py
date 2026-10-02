"""A limit that rolls over (#360).

Leftover keeps stacking month after month, like an envelope, and
overspending carries too. A month that ran without the limit, or with
rollover off, ends the run; turning it back on starts fresh. Nothing is
recorded for it: the carry is read from each month's own limits and its
transactions, so it changes only when they do.
"""

from datetime import date

import pytest

from app.services.finance.service import FinanceService
from tests.services._finance_factories import (
    budget_points,
    seed_account,
    seed_limit,
    seed_txn,
)

# Each rolling limit a test seeds answers with its carry, and each month it
# reads back carries again: the history read repeats by design here.
pytestmark = pytest.mark.queryspy(threshold=5)

AUGUST, SEPTEMBER, OCTOBER = 202608, 202609, 202610
TODAY = date(2026, 10, 15)


async def _groceries(svc: FinanceService) -> int:
    category = await svc.get_or_create_category_from_hint("Food:Groceries")
    assert category.id is not None
    return category.id


async def _spend(svc: FinanceService, category_id: int, spends: dict[int, int]) -> None:
    """``{period: cents}`` of groceries, one charge each month."""
    account = await seed_account(svc)
    for period, cents in spends.items():
        await seed_txn(
            svc,
            account.id,
            -cents,
            date(period // 100, period % 100, 10),
            category_id=category_id,
        )


async def _october(svc: FinanceService, category_id: int):
    summary = await svc.budget_summary(
        owner_user_id=1, period_month=OCTOBER, today=TODAY
    )
    return next(
        line
        for line in summary.bucket("flexible").lines
        if line.category_id == category_id
    )


class TestTheCarry:
    @pytest.mark.asyncio
    async def test_leftover_keeps_stacking(self, svc: FinanceService) -> None:
        """$50 left in August and $30 in September: October's $200 reads $280."""
        groceries = await _groceries(svc)
        for period in (AUGUST, SEPTEMBER, OCTOBER):
            await seed_limit(svc, groceries, 20_000, period_month=period, rollover=True)
        await _spend(
            svc, groceries, {AUGUST: 15_000, SEPTEMBER: 17_000, OCTOBER: 25_000}
        )

        line = await _october(svc, groceries)

        assert (line.rollover, line.carried_amount) == (True, 8_000)
        assert line.available_amount == 28_000
        # $250 of $280 is 89%: a warning, not the overrun $250 of $200 would be.
        assert (line.spent_percent, line.status) == (89, "warn")

    @pytest.mark.asyncio
    async def test_overspending_carries_too(self, svc: FinanceService) -> None:
        groceries = await _groceries(svc)
        for period in (AUGUST, SEPTEMBER, OCTOBER):
            await seed_limit(svc, groceries, 20_000, period_month=period, rollover=True)
        await _spend(svc, groceries, {AUGUST: 15_000, SEPTEMBER: 24_000})

        line = await _october(svc, groceries)

        assert line.carried_amount == 5_000 - 4_000

    @pytest.mark.asyncio
    async def test_rollover_off_ends_the_run(self, svc: FinanceService) -> None:
        """August's leftover is gone: it was not rolling over then."""
        groceries = await _groceries(svc)
        await seed_limit(svc, groceries, 20_000, period_month=AUGUST, rollover=False)
        for period in (SEPTEMBER, OCTOBER):
            await seed_limit(svc, groceries, 20_000, period_month=period, rollover=True)
        await _spend(svc, groceries, {AUGUST: 5_000, SEPTEMBER: 17_000})

        assert (await _october(svc, groceries)).carried_amount == 3_000

    @pytest.mark.asyncio
    async def test_a_month_without_the_limit_ends_the_run(
        self, svc: FinanceService
    ) -> None:
        groceries = await _groceries(svc)
        fuel = await svc.get_or_create_category_from_hint("Auto:Fuel")
        await seed_limit(svc, groceries, 20_000, period_month=AUGUST, rollover=True)
        # September ran on fuel alone: groceries' run stops there.
        await seed_limit(svc, fuel.id, 5_000, period_month=SEPTEMBER)
        await svc.delete_budget_line(
            (await _line_for(svc, SEPTEMBER, groceries)), owner_user_id=1
        )
        await seed_limit(svc, groceries, 20_000, period_month=OCTOBER, rollover=True)
        await _spend(svc, groceries, {AUGUST: 5_000})

        assert (await _october(svc, groceries)).carried_amount == 0

    @pytest.mark.asyncio
    async def test_a_month_nobody_opened_ran_on_the_one_before(
        self, svc: FinanceService
    ) -> None:
        """September has no limits of its own, so it ran on August's: its
        leftover against August's $200 carries."""
        groceries = await _groceries(svc)
        await seed_limit(svc, groceries, 20_000, period_month=AUGUST, rollover=True)
        await seed_limit(svc, groceries, 20_000, period_month=OCTOBER, rollover=True)
        await _spend(svc, groceries, {AUGUST: 15_000, SEPTEMBER: 12_000})

        assert (await _october(svc, groceries)).carried_amount == 5_000 + 8_000

    @pytest.mark.asyncio
    async def test_a_limit_that_does_not_roll_over_carries_nothing(
        self, svc: FinanceService
    ) -> None:
        groceries = await _groceries(svc)
        for period in (AUGUST, SEPTEMBER, OCTOBER):
            await seed_limit(svc, groceries, 20_000, period_month=period)
        await _spend(svc, groceries, {AUGUST: 5_000})

        line = await _october(svc, groceries)

        assert (line.rollover, line.carried_amount) == (False, 0)
        assert line.available_amount == 20_000


class TestSettingIt:
    @pytest.mark.asyncio
    async def test_setting_a_limit_again_leaves_rollover_as_it_was(
        self, svc: FinanceService
    ) -> None:
        """A dialog, a suggestion or the goal box sets an amount; only the
        row's own checkbox and Illiana's card say rollover."""
        groceries = await _groceries(svc)
        await seed_limit(svc, groceries, 20_000, period_month=OCTOBER, rollover=True)

        line = await seed_limit(svc, groceries, 25_000, period_month=OCTOBER)

        assert (line.allocated_amount, line.rollover) == (25_000, True)

    @pytest.mark.asyncio
    async def test_the_answer_to_an_edit_carries_what_the_page_shows(
        self, svc: FinanceService
    ) -> None:
        groceries = await _groceries(svc)
        for period in (SEPTEMBER, OCTOBER):
            await seed_limit(svc, groceries, 20_000, period_month=period, rollover=True)
        await _spend(svc, groceries, {SEPTEMBER: 17_000})

        line = await seed_limit(svc, groceries, 22_000, period_month=OCTOBER)

        assert (line.carried_amount, line.available_amount) == (3_000, 25_000)


class TestTheProjection:
    @pytest.mark.asyncio
    async def test_this_months_draw_spends_what_it_carried(
        self, svc: FinanceService
    ) -> None:
        """The Projected tab draws what is left of a limit at month end; a
        limit that rolls over has its carry left as well."""
        groceries = await _groceries(svc)
        for period in (SEPTEMBER, OCTOBER):
            await seed_limit(svc, groceries, 20_000, period_month=period, rollover=True)
        await _spend(svc, groceries, {SEPTEMBER: 17_000, OCTOBER: 5_000})

        result = await svc.project_balances(owner_user_id=1, days=20, today=TODAY)

        (october,) = budget_points(result, "Food:Groceries")
        assert october.amount == -(20_000 + 3_000 - 5_000)


async def _line_for(svc: FinanceService, period: int, category_id: int) -> int:
    summary = await svc.budget_summary(
        owner_user_id=1, period_month=period, today=TODAY
    )
    return next(
        line.id
        for line in summary.bucket("flexible").lines
        if line.category_id == category_id
    )
