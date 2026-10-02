"""Net worth by component (#343).

The same per-account daily balances the filtered net worth line already
sums, grouped by account type the way the Accounts page groups them, and
the net worth with the house and the loan it secures left out.
"""

from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import networth
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account


async def _book(svc: FinanceService, db: AsyncSession) -> None:
    """Checking $100, a $500k house with a $300k mortgage on it, a card at
    $25 owed."""
    await seed_account(svc, current_balance=10_000)
    house = await seed_account(
        svc, name="House", account_type="property", classification="asset"
    )
    assert house.id is not None
    await svc.upsert_valuation(
        account_id=house.id,
        owner_user_id=1,
        as_of_date=current_date() - timedelta(days=2),
        value=50_000_000,
    )
    mortgage = await seed_account(
        svc,
        name="Mortgage",
        account_type="loan",
        classification="liability",
        current_balance=30_000_000,
    )
    assert mortgage.id is not None
    await svc.set_secured_debt(
        mortgage.id, owner_user_id=1, secured_by_account_id=house.id
    )
    await seed_account(
        svc,
        name="Visa",
        account_type="credit_card",
        classification="liability",
        current_balance=2_500,
    )
    await networth.recompute_snapshots(db, owner_user_id=1)


class TestByType:
    @pytest.mark.asyncio
    async def test_each_group_is_a_line_and_they_sum_to_the_net(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await _book(svc, async_db_session)

        parts = await svc.net_worth_by_type(owner_user_id=1, days=30)
        net = await svc.get_net_worth_series(owner_user_id=1, days=30)

        latest = {group.label: group.values[-1] for group in parts.groups}
        assert latest == {
            "Banking": 10_000,
            "Credit Cards": -2_500,
            "Property": 50_000_000,
            "Loans & Debt": -30_000_000,
        }
        assert [g.label for g in parts.groups] == [  # the Accounts page's order
            "Banking",
            "Credit Cards",
            "Property",
            "Loans & Debt",
        ]
        assert parts.dates[-1] == net[-1].as_of_date
        assert sum(latest.values()) == net[-1].net_worth_amount


class TestWithoutTheHouse:
    @pytest.mark.asyncio
    async def test_the_house_and_its_mortgage_leave_together(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await _book(svc, async_db_session)

        series = await svc.get_net_worth_series(
            owner_user_id=1, days=30, without_house=True
        )

        latest = series[-1]
        assert (latest.total_assets_amount, latest.total_liabilities_amount) == (
            10_000,
            2_500,
        )
        assert latest.as_of_date == current_date()

    @pytest.mark.asyncio
    async def test_an_account_filter_still_applies(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await _book(svc, async_db_session)
        accounts, _total = await svc.list_accounts(owner_user_id=1)
        visa = next(a for a in accounts if a.name == "Visa")

        series = await svc.get_net_worth_series(
            owner_user_id=1, days=30, account_ids=[visa.id], without_house=True
        )

        assert series[-1].net_worth_amount == -2_500
