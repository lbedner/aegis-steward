"""A budget limit as a card (#265): she proposes, a person decides.

She could read every limit (``budget``) and change none: asked to "put
something together" to cut spending, she described a plan ("lower the
cannabis limit from $500 to $300") and said she could change it, with no
card to change it through. Nothing moves money; the card says what the
month allows before and after.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.planning.budgets.lines import lines_in_force
from app.services.finance.domains.writes.budgets import (
    BudgetLimitPayload,
    budget_limit_describe,
    budget_limit_execute,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date, current_period_month
from tests.services._finance_factories import seed_account, seed_category


async def _said(db: AsyncSession, payload: BudgetLimitPayload) -> dict[str, str]:
    return {row.label: row.value for row in await budget_limit_describe(db, payload, None)}


async def _limit(
    db: AsyncSession, category_id: int | None, cents: int, month: int | None = None
) -> Any:
    return await FinanceService(db).upsert_budget_line(
        owner_user_id=None,
        period_month=month,
        category_id=category_id,
        payee_key=None,
        payee_label=None,
        allocated_amount=cents,
    )


async def _month_lines(db: AsyncSession, month: int) -> dict[Any, int]:
    budget = await FinanceService(db).get_or_create_budget(
        owner_user_id=None, period_month=month
    )
    assert budget.id is not None
    lines = await lines_in_force(db, budget_id=budget.id, period_month=month)
    return {line.category_id or line.payee_key: line.allocated_amount for line in lines}


def _next_month(month: int) -> int:
    year, number = divmod(month, 100)
    return (year + 1) * 100 + 1 if number == 12 else month + 1


class TestTheCard:
    @pytest.mark.asyncio
    async def test_it_shows_the_limit_before_and_after(
        self, async_db_session: AsyncSession
    ) -> None:
        cannabis = await seed_category(async_db_session, "Entertainment:Canibus")
        await _limit(async_db_session, cannabis.id, 50_000)

        said = await _said(
            async_db_session, BudgetLimitPayload(category_id=cannabis.id, limit_cents=30_000)
        )

        assert said["Limit"] == "Entertainment:Canibus"
        assert said["Per month"] == "$500.00 → $300.00"
        assert "Money" in said  # a limit moves none

    @pytest.mark.asyncio
    async def test_a_new_line_reads_as_none_before(
        self, async_db_session: AsyncSession
    ) -> None:
        eating_out = await seed_category(async_db_session, "Food & Dining:Restaurants")
        said = await _said(
            async_db_session, BudgetLimitPayload(category_id=eating_out.id, limit_cents=30_000)
        )
        assert said["Per month"] == "none → $300.00"

    @pytest.mark.asyncio
    async def test_a_card_that_changes_nothing_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        cannabis = await seed_category(async_db_session, "Entertainment:Canibus")
        await _limit(async_db_session, cannabis.id, 30_000)
        with pytest.raises(ValueError, match="nothing"):
            await _said(
                async_db_session,
                BudgetLimitPayload(category_id=cannabis.id, limit_cents=30_000),
            )

    @pytest.mark.asyncio
    async def test_an_unknown_category_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="category"):
            await _said(
                async_db_session, BudgetLimitPayload(category_id=999_999, limit_cents=100)
            )


class TestApproving:
    @pytest.mark.asyncio
    async def test_it_sets_the_limit_and_leaves_the_others(
        self, async_db_session: AsyncSession
    ) -> None:
        cannabis = await seed_category(async_db_session, "Entertainment:Canibus")
        groceries = await seed_category(async_db_session, "Food & Dining:Groceries")
        await _limit(async_db_session, cannabis.id, 50_000)
        await _limit(async_db_session, groceries.id, 100_000)

        await budget_limit_execute(
            async_db_session,
            BudgetLimitPayload(category_id=cannabis.id, limit_cents=30_000),
            None,
        )

        month = current_period_month()
        assert await _month_lines(async_db_session, month) == {
            cannabis.id: 30_000,
            groceries.id: 100_000,
        }

    @pytest.mark.asyncio
    async def test_next_month_keeps_the_limits_it_inherits(
        self, async_db_session: AsyncSession
    ) -> None:
        """A month with no lines of its own runs on the last month's. Writing
        one line into it used to leave it with ONLY that line - every other
        limit gone for that month, and for every month copied from it."""
        cannabis = await seed_category(async_db_session, "Entertainment:Canibus")
        groceries = await seed_category(async_db_session, "Food & Dining:Groceries")
        await _limit(async_db_session, cannabis.id, 50_000)
        await _limit(async_db_session, groceries.id, 100_000)
        upcoming = _next_month(current_period_month())

        await budget_limit_execute(
            async_db_session,
            BudgetLimitPayload(category_id=cannabis.id, limit_cents=30_000, month=upcoming),
            None,
        )

        assert await _month_lines(async_db_session, upcoming) == {
            cannabis.id: 30_000,
            groceries.id: 100_000,
        }
        # This month is untouched.
        assert (await _month_lines(async_db_session, current_period_month()))[
            cannabis.id
        ] == 50_000


class TestAPayeeLine:
    @pytest.mark.asyncio
    async def test_a_payee_named_in_words_becomes_its_own_line(
        self, async_db_session: AsyncSession
    ) -> None:
        """"Keep Starbucks to $150": the payee is found the way the Budget
        page's goal box finds it, from the last 90 days of spending."""
        svc = FinanceService(async_db_session)
        account = await seed_account(svc)
        for days in (3, 10):
            await svc.create_transaction(
                account_id=account.id,
                amount=-650,
                txn_date=current_date() - timedelta(days=days),
                owner_user_id=1,
                name="Starbucks",
            )
        await async_db_session.flush()
        payload = BudgetLimitPayload(payee="Starbucks", limit_cents=15_000)

        said = await _said(async_db_session, payload)
        assert said["Limit"] == "Starbucks"
        assert said["Per month"] == "none → $150.00"

        await budget_limit_execute(async_db_session, payload, None)
        lines = await _month_lines(async_db_session, current_period_month())
        assert list(lines.values()) == [15_000]
        assert all(isinstance(key, str) for key in lines)  # a payee line

    @pytest.mark.asyncio
    async def test_a_payee_with_no_spending_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="payee"):
            await _said(
                async_db_session, BudgetLimitPayload(payee="Nowhere Cafe", limit_cents=100)
            )


class TestThePayload:
    @pytest.mark.parametrize(
        "fields",
        [
            {"limit_cents": 100},  # no target
            {"category_id": 1, "payee": "Starbucks", "limit_cents": 100},  # two
            {"category_id": 1, "limit_cents": -1},
            {"category_id": 1, "limit_cents": 100, "month": 202613},
        ],
    )
    def test_nonsense_is_refused(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            BudgetLimitPayload(**fields)


def test_it_is_registered_and_advertised() -> None:
    from app.services.finance.domains import writes
    from app.services.finance.domains.detection.analyst.prompt_changes import (
        PROPOSING_CHANGES,
    )

    assert "budget.limit" in writes.registered_change_types()
    assert "`budget.limit`" in PROPOSING_CHANGES
