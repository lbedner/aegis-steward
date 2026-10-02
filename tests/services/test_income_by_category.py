"""Income by category (#344): the spending rollup, the other way round.

Money in per parent category over a window, largest first, as positive
amounts - the same predicate and the same parent rollup as spending by
category, so the cash-flow page's two tables read alike.
"""

from datetime import timedelta

import pytest

from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account, seed_txn


class TestIncomeByCategory:
    @pytest.mark.asyncio
    async def test_money_in_rolls_up_to_its_parent(self, svc: FinanceService) -> None:
        salary = await svc.get_or_create_category_from_hint("Income:Salary")
        interest = await svc.get_or_create_category_from_hint("Income:Interest")
        refund = await svc.get_or_create_category_from_hint("Refunds:Store")
        groceries = await svc.get_or_create_category_from_hint("Food:Groceries")
        account = await seed_account(svc)
        today = current_date()
        await seed_txn(svc, account.id, 300_000, today, category_id=salary.id)
        await seed_txn(svc, account.id, 1_200, today, category_id=interest.id)
        await seed_txn(svc, account.id, 2_500, today, category_id=refund.id)
        await seed_txn(svc, account.id, -9_000, today, category_id=groceries.id)

        rows = await svc.income_by_category(owner_user_id=1, days=30)

        assert rows == [("Income", 301_200), ("Refunds", 2_500)]

    @pytest.mark.asyncio
    async def test_it_stops_at_the_last_day_asked(self, svc: FinanceService) -> None:
        salary = await svc.get_or_create_category_from_hint("Income:Salary")
        account = await seed_account(svc)
        today = current_date()
        await seed_txn(svc, account.id, 300_000, today, category_id=salary.id)
        await seed_txn(
            svc, account.id, 100_000, today - timedelta(days=3), category_id=salary.id
        )

        rows = await svc.income_by_category(
            owner_user_id=1, days=30, through=today - timedelta(days=1)
        )

        assert rows == [("Income", 100_000)]
