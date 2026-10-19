"""A refund nets against what it refunds (#316).

Income was every credit that was not a transfer, so a return filed under
groceries read as income, and spending never came down for it: both
looked inflated after returns. Now what a row counts as follows what it
is filed under. A credit under a spending category reduces that
category; income is what is filed as income; a credit nobody filed is
counted as neither, and said, so it can be filed.
"""

from __future__ import annotations

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import cash_flow
from app.services.finance.domains.planning import queries as planning
from app.services.finance.domains.planning.budgets import month_actuals
from app.services.finance.schemas import SplitPart
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account, seed_txn


async def _month(svc: FinanceService) -> dict[str, int]:
    """Salary $3,000 (filed as income), groceries $90 with a $25 return
    filed under groceries, and a $50 credit nobody filed."""
    salary = await svc.get_or_create_category_from_hint("Income:Salary")
    groceries = await svc.get_or_create_category_from_hint("Food:Groceries")
    account = await seed_account(svc)
    today = current_date().replace(day=1)
    await seed_txn(svc, account.id, 300_000, today, category_id=salary.id)
    await seed_txn(svc, account.id, -9_000, today, category_id=groceries.id)
    await seed_txn(svc, account.id, 2_500, today, category_id=groceries.id)
    await seed_txn(svc, account.id, 5_000, today)
    assert groceries.id is not None and account.id is not None
    return {"groceries": groceries.id, "account": account.id}


class TestTheTotals:
    @pytest.mark.asyncio
    async def test_a_return_brings_spending_down_and_is_not_income(
        self, svc: FinanceService
    ) -> None:
        await _month(svc)

        flow = await svc.cash_flow(owner_user_id=1, start=current_date().replace(day=1))

        assert (flow.income, flow.spending, flow.unfiled) == (300_000, 6_500, 5_000)

    @pytest.mark.asyncio
    async def test_the_months_bars_count_the_same(self, svc: FinanceService) -> None:
        await _month(svc)

        bars = await svc.monthly_cashflow(owner_user_id=1, months=1)

        assert (bars[-1].income, bars[-1].expense, bars[-1].unfiled) == (
            300_000,
            6_500,
            5_000,
        )

    @pytest.mark.asyncio
    async def test_so_does_the_month_so_far_and_the_pace(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await _month(svc)
        today = current_date()

        so_far = await cash_flow.month_to_date(
            async_db_session, owner_user_id=1, today=today
        )
        pace = await cash_flow.spending_pace(
            async_db_session, owner_user_id=1, today=today
        )

        assert (so_far.this_month.income, so_far.this_month.spending) == (
            300_000,
            6_500,
        )
        assert pace.this_month[-1] == 6_500


class TestByCategory:
    @pytest.mark.asyncio
    async def test_the_category_nets_and_income_is_only_income(
        self, svc: FinanceService
    ) -> None:
        await _month(svc)

        spending = await svc.spending_by_category(owner_user_id=1, days=40)
        income = await svc.income_by_category(owner_user_id=1, days=40)

        assert spending == [("Food", 6_500)]
        assert income == [("Income", 300_000)]

    @pytest.mark.asyncio
    async def test_a_budget_line_is_spent_net_of_its_returns(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        ids = await _month(svc)
        start = current_date().replace(day=1)

        spent = await planning.spend_by_category(
            async_db_session,
            owner_user_id=1,
            start=start,
            category_ids=[ids["groceries"]],
        )
        actuals = await month_actuals(async_db_session, owner_user_id=1)
        money_in, money_out, unfiled = (
            actuals.money_in,
            actuals.money_out,
            actuals.unfiled,
        )

        assert spent == {ids["groceries"]: 6_500}
        assert (money_in, money_out, unfiled) == (300_000, 6_500, 5_000)


class TestSplits:
    @pytest.mark.asyncio
    async def test_each_line_counts_as_what_it_is_filed_under(
        self, svc: FinanceService
    ) -> None:
        """A deposit that is mostly pay and partly a return."""
        salary = await svc.get_or_create_category_from_hint("Income:Salary")
        groceries = await svc.get_or_create_category_from_hint("Food:Groceries")
        account = await seed_account(svc)
        day = current_date().replace(day=1)
        await seed_txn(svc, account.id, -9_000, day, category_id=groceries.id)
        deposit = await seed_txn(svc, account.id, 100_000, day)
        assert deposit.id is not None
        await svc.split_transaction(
            deposit.id,
            [
                SplitPart(category_id=salary.id, amount=97_500),
                SplitPart(category_id=groceries.id, amount=2_500),
            ],
            owner_user_id=1,
        )

        flow = await svc.cash_flow(owner_user_id=1, start=day)

        assert (flow.income, flow.spending, flow.unfiled) == (97_500, 6_500, 0)

    @pytest.mark.asyncio
    async def test_the_months_of_expenses_read_counts_the_lines_too(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A $2,000 payment filed as a transfer, split $1,500 principal
        and $500 interest: every read counted the $500 but the one that
        sizes a fund in months of expenses, which judged the whole
        payment a transfer and counted nothing."""
        from app.services.finance.domains.ledger.queries import (
            outflow_by_account_in_window,
        )

        moved = await svc.get_or_create_category_from_hint("Transfer:Mortgage")
        principal = await svc.get_or_create_category_from_hint(
            "Transfer:Loan principal"
        )
        housing = await svc.get_or_create_category_from_hint("Housing:Mortgage")
        account = await seed_account(svc)
        day = current_date().replace(day=1)
        payment = await seed_txn(svc, account.id, -200_000, day, category_id=moved.id)
        assert payment.id is not None
        await svc.split_transaction(
            payment.id,
            [
                SplitPart(category_id=principal.id, amount=150_000),
                SplitPart(category_id=housing.id, amount=50_000),
            ],
            owner_user_id=1,
        )

        out = await outflow_by_account_in_window(
            async_db_session, owner_user_id=1, start=day, end=current_date()
        )

        assert out == [(account.id, 50_000)]


class TestTheirMoney:
    @pytest.mark.asyncio
    async def test_what_comes_and_goes_in_their_account_is_not_ours(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A parent's checking, held here for them (#314): out of net worth
        already, and out of our income and spending too."""
        from app.services.finance.domains.ledger.subjects import subject_for_party
        from app.services.matters.service import PartyService

        ids = await _month(svc)
        party = await PartyService(async_db_session).create(
            name="Refunds Net Parent", kind="person"
        )
        await async_db_session.flush()
        assert party.id is not None
        subject = await subject_for_party(
            async_db_session, party.id, name=party.name, owner_user_id=1
        )
        theirs = await seed_account(svc, "Their Checking")
        theirs.subject_id = subject.id
        async_db_session.add(theirs)
        salary = await svc.get_or_create_category_from_hint("Income:Salary")
        start = current_date().replace(day=1)
        await seed_txn(svc, theirs.id, 150_000, start, category_id=salary.id)
        await seed_txn(svc, theirs.id, -4_000, start, category_id=ids["groceries"])

        flow = await svc.cash_flow(owner_user_id=1, start=start)
        spent = await planning.spend_by_category(
            async_db_session,
            owner_user_id=1,
            start=start,
            category_ids=[ids["groceries"]],
        )

        assert (flow.income, flow.spending) == (300_000, 6_500)
        assert spent == {ids["groceries"]: 6_500}
        assert await svc.spending_by_category(owner_user_id=1, days=40) == [
            ("Food", 6_500)
        ]
