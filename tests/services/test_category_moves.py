"""Which categories moved this month (#346).

This month to date beside the same days of last month and of the typical
month, the categories that changed most first: the overspend alert's
on-pace figures, about everyday spending. A bill is left out (Bills &
Income has it), and so is a category with nothing spent yet this month -
on the 2nd, the mortgage that goes out on the 5th has not "moved".
"""

from datetime import date

import pytest

from app.services.finance.service import FinanceService
from tests.services._finance_factories import (
    category_id,
    seed_account,
    seed_monthly_spend,
    seed_stream,
    seed_txn,
)

JULY, AUGUST, SEPTEMBER, OCTOBER = 202607, 202608, 202609, 202610
TODAY = date(2026, 10, 15)


class TestWhatMoved:
    @pytest.mark.asyncio
    async def test_the_biggest_change_against_the_typical_month_leads(
        self, svc: FinanceService
    ) -> None:
        """Dining doubled; groceries barely moved; fuel has not come yet."""
        dining = await category_id(svc, "Food:Restaurants")
        groceries = await category_id(svc, "Food:Groceries")
        fuel = await category_id(svc, "Auto:Fuel")
        await seed_monthly_spend(
            svc,
            dining,
            {JULY: 20_000, AUGUST: 21_000, SEPTEMBER: 22_000, OCTOBER: 45_000},
        )
        await seed_monthly_spend(
            svc,
            groceries,
            {JULY: 40_000, AUGUST: 41_000, SEPTEMBER: 42_000, OCTOBER: 43_000},
        )
        await seed_monthly_spend(
            svc, fuel, {JULY: 9_000, AUGUST: 9_000, SEPTEMBER: 9_000}
        )

        moves = await svc.category_moves(owner_user_id=1, today=TODAY)

        assert [
            (m.category_id, m.this_month, m.last_month, m.typical) for m in moves
        ] == [
            (dining, 45_000, 22_000, 21_000),  # median of the earlier months
            (groceries, 43_000, 42_000, 41_000),
        ]
        assert fuel not in {m.category_id for m in moves}
        assert (
            moves[0].name
            == (await svc.get_or_create_category_from_hint("Food:Restaurants")).name
        )
        assert (moves[0].change, moves[1].change) == (24_000, 2_000)

    @pytest.mark.asyncio
    async def test_earlier_months_count_only_to_the_same_day(
        self, svc: FinanceService
    ) -> None:
        """On the 15th, September's charge on the 20th is not yet comparable."""
        dining = await category_id(svc, "Food:Restaurants")
        account = await seed_account(svc)
        await seed_txn(svc, account.id, -5_000, date(2026, 9, 5), category_id=dining)
        await seed_txn(svc, account.id, -70_000, date(2026, 9, 20), category_id=dining)
        await seed_txn(svc, account.id, -6_000, date(2026, 10, 3), category_id=dining)

        (move,) = await svc.category_moves(owner_user_id=1, today=TODAY)

        assert (move.this_month, move.last_month, move.typical) == (6_000, 5_000, 5_000)

    @pytest.mark.asyncio
    async def test_a_new_category_has_nothing_to_move_from(
        self, svc: FinanceService
    ) -> None:
        pets = await category_id(svc, "Pets:Supplies")
        await seed_monthly_spend(svc, pets, {OCTOBER: 3_000})

        (move,) = await svc.category_moves(owner_user_id=1, today=TODAY)

        assert (move.typical, move.change) == (None, 3_000)

    @pytest.mark.asyncio
    async def test_a_bill_is_not_a_move(
        self, svc: FinanceService, async_db_session
    ) -> None:
        """The mortgage is a bill: its payments are Bills & Income's, early
        or late, and never what "moved"."""
        housing = await category_id(svc, "Home:Mortgage & Rent")
        account = await seed_account(svc)
        mortgage = await seed_stream(
            svc,
            name="Mortgage",
            expected_amount=250_000,
            next_expected_date=date(2026, 11, 1),
        )
        for day in (date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)):
            payment = await seed_txn(
                svc, account.id, -260_000, day, category_id=housing
            )
            payment.recurring_stream_id = mortgage.id
            async_db_session.add(payment)
        await seed_txn(svc, account.id, -4_000, date(2026, 10, 3), category_id=housing)
        await async_db_session.flush()

        (move,) = await svc.category_moves(owner_user_id=1, today=TODAY)

        assert (move.category_id, move.this_month, move.typical) == (
            housing,
            4_000,
            None,
        )
