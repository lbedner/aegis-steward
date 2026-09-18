"""What is scheduled against one account, read in one place.

Illiana quoted a net scheduled change for Chase checking off her own
arithmetic while the account's page showed nothing at all. Two answers
to one question is how a page and an agent come to disagree in front of
somebody, so the sum lives once and both read it.
"""

from datetime import date, timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.planning.recurring.upcoming import (
    WINDOW_DAYS,
    Scheduled,
    scheduled_by_account,
    totalled,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account


async def _stream(
    db: AsyncSession, account_id: int, name: str, cents: int, direction: str, when: date
) -> None:
    from app.services.finance.domains.planning.recurring import streams

    await streams.create_recurring_stream(
        db,
        owner_user_id=None,
        name=name,
        direction=direction,
        frequency="monthly",
        expected_amount=cents,
        next_expected_date=when,
        account_id=account_id,
    )


class TestWhatIsComing:
    @pytest.mark.asyncio
    async def test_only_what_falls_inside_the_window_and_names_an_account(
        self, async_db_session: AsyncSession
    ) -> None:
        today = current_date()
        checking = await seed_account(FinanceService(async_db_session), name="Checking")
        await _stream(
            async_db_session, int(checking.id), "Rent", 150_000, "outflow", today
        )
        await _stream(
            async_db_session,
            int(checking.id),
            "Pay",
            90_00,
            "inflow",
            today + timedelta(days=3),
        )
        await _stream(
            async_db_session,
            int(checking.id),
            "Far off",
            50_000,
            "outflow",
            today + timedelta(days=WINDOW_DAYS + 5),
        )
        await _stream(
            async_db_session,
            int(checking.id),
            "Gone by",
            7_000,
            "outflow",
            today - timedelta(days=2),
        )
        await async_db_session.commit()

        found = await scheduled_by_account(async_db_session)
        names = [item.name for item in found[int(checking.id)]]
        assert names == ["Rent", "Pay"]

    @pytest.mark.asyncio
    async def test_an_outflow_is_negative_and_an_inflow_is_positive(
        self, async_db_session: AsyncSession
    ) -> None:
        today = current_date()
        checking = await seed_account(FinanceService(async_db_session), name="Signs")
        await _stream(
            async_db_session, int(checking.id), "Bill", 20_000, "outflow", today
        )
        await _stream(
            async_db_session, int(checking.id), "Wages", 50_000, "inflow", today
        )
        await async_db_session.commit()

        by_amount = {
            item.name: item.amount_cents
            for item in (await scheduled_by_account(async_db_session))[int(checking.id)]
        }
        assert by_amount == {"Bill": -20_000, "Wages": 50_000}


class TestTheSum:
    def test_it_adds_the_two_directions_separately_and_together(self) -> None:
        when = date(2026, 10, 1)
        total = totalled(
            [
                Scheduled(name="Bill", when=when, amount_cents=-20_000),
                Scheduled(name="Other bill", when=when, amount_cents=-5_000),
                Scheduled(name="Wages", when=when, amount_cents=50_000),
            ]
        )
        assert (total.items, total.inflow, total.outflow, total.net) == (
            3,
            50_000,
            25_000,
            25_000,
        )

    def test_nothing_scheduled_sums_to_nothing(self) -> None:
        assert totalled([]).net == 0
        assert totalled([]).items == 0
