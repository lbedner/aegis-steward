"""One net worth (#314): the line, the headline and the API count the
same accounts by the same rule.

The nightly line summed every live account - a goal's hidden pot, a
parent's house held here for them - while the headline summed only our
own visible ones, so the two disagreed by whatever the household was
holding for someone else. The API's net worth summed raw balances, so a
synced debt (stored negative) was added instead of subtracted.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import networth
from app.services.finance.domains.ledger.subjects import subject_for_party
from app.services.finance.models import FinanceAccount, FinanceNetWorthSnapshot
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from app.services.matters.service import PartyService
from tests.services._finance_factories import seed_account


async def _book(svc: FinanceService, db: AsyncSession) -> dict[str, FinanceAccount]:
    """Checking $1,000 and a card owing $200 (stored negative, as a sync
    stores it), a hidden pot holding $90, and a house held for someone."""
    checking = await seed_account(svc, current_balance=100_000)
    card = await seed_account(
        svc,
        "Card",
        "credit_card",
        "liability",
        current_balance=-20_000,
    )
    pot = await seed_account(svc, "Vacation", "goal", current_balance=9_000)
    pot.is_hidden = True
    party = await PartyService(db).create(name="Net Worth Parent", kind="person")
    await db.flush()
    theirs = await seed_account(
        svc, "Their House", "property", current_balance=30_000_000
    )
    assert party.id is not None
    subject = await subject_for_party(db, party.id, name=party.name, owner_user_id=1)
    theirs.subject_id = subject.id
    db.add_all([pot, theirs])
    await db.flush()
    return {"checking": checking, "card": card, "pot": pot, "theirs": theirs}


async def _line(db: AsyncSession) -> dict[object, int]:
    rows = (await db.exec(select(FinanceNetWorthSnapshot))).all()
    return {r.as_of_date: r.net_worth_amount for r in rows}


class TestOneNetWorth:
    @pytest.mark.asyncio
    async def test_the_line_and_the_total_agree(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await _book(svc, async_db_session)

        await networth.recompute_snapshots(async_db_session, owner_user_id=1)
        total = await networth.get_net_worth(async_db_session, owner_user_id=1)

        assert (await _line(async_db_session))[current_date()] == 80_000
        assert total.net_worth_amount == 80_000
        assert total.total_liabilities_amount == 20_000

    @pytest.mark.asyncio
    async def test_a_closed_account_leaves_from_its_close_date(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await _book(svc, async_db_session)
        closed_on = current_date() - timedelta(days=3)
        old = await seed_account(svc, "Old Savings", "savings", current_balance=5_000)
        assert old.id is not None
        await svc.update_account(
            old.id, owner_user_id=1, is_closed=True, closed_on=closed_on
        )

        await networth.recompute_snapshots(async_db_session, owner_user_id=1)

        line = await _line(async_db_session)
        assert line[closed_on - timedelta(days=1)] == 85_000
        assert line[closed_on] == 80_000
        total = await networth.get_net_worth(async_db_session, owner_user_id=1)
        assert total.net_worth_amount == 80_000

    @pytest.mark.asyncio
    async def test_a_removed_account_leaves_every_day_not_just_the_rebuilt(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The nightly rebuild covers 35 days; a removal rebuilds all of it."""
        await seed_account(svc, current_balance=100_000)
        extra = await seed_account(svc, "Duplicate", current_balance=7_000)
        start = current_date() - timedelta(days=90)
        extra.balance_as_of = datetime.combine(start, time())
        async_db_session.add(extra)
        await networth.recompute_snapshots(
            async_db_session, owner_user_id=1, start_date=start
        )

        assert extra.id is not None
        await svc.soft_delete_account(extra.id, owner_user_id=1)

        assert set((await _line(async_db_session)).values()) == {100_000}

    @pytest.mark.asyncio
    async def test_a_redraw_keeps_what_each_account_was_worth_then(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """An IRA recorded at $8,000 two months ago and worth $10,000 now:
        redrawing the line re-sums that day, and never carries today's
        balance back over it."""
        from app.services.finance.domains.ledger.accounts import redraw_line
        from app.services.finance.models import FinanceBalanceSnapshot

        ira = await seed_account(svc, "IRA", "brokerage", current_balance=1_000_000)
        assert ira.id is not None
        then = current_date() - timedelta(days=60)
        async_db_session.add_all(
            [
                FinanceBalanceSnapshot(
                    account_id=ira.id,
                    owner_user_id=1,
                    balance_date=then,
                    balance=800_000,
                    source="sync",
                ),
                FinanceNetWorthSnapshot(
                    owner_user_id=1, as_of_date=then, net_worth_amount=0
                ),
            ]
        )
        await async_db_session.flush()

        await redraw_line(async_db_session, 1)

        line = await _line(async_db_session)
        assert (line[then], line[current_date()]) == (800_000, 1_000_000)

    @pytest.mark.asyncio
    async def test_by_type_a_closed_account_stays_in_the_days_before(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The groups add up to the line, closed accounts included."""
        await seed_account(svc, current_balance=100_000)
        old = await seed_account(svc, "Old Savings", "savings", current_balance=5_000)
        assert old.id is not None
        closed_on = current_date() - timedelta(days=3)
        await svc.update_account(
            old.id, owner_user_id=1, is_closed=True, closed_on=closed_on
        )

        parts = await networth.net_worth_by_type(
            async_db_session, owner_user_id=1, days=10
        )

        (banking,) = parts.groups
        by_day = dict(zip(parts.dates, banking.values, strict=True))
        assert by_day[closed_on - timedelta(days=1)] == 105_000
        assert by_day[closed_on] == 100_000


class TestEveryFigureMovesTogether:
    """The second dry run's findings: figures that disagreed with the
    line, and changes the line missed until the next night."""

    @pytest.mark.asyncio
    async def test_the_headline_sums_a_debt_as_the_line_does(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A loan with no balance ever set, whose register holds only the
        $500 its balance came down by: the API took it as $500 owed, the
        Overview headline as $500 more, $1,000 apart."""
        from app.components.web_frontend.routes.finance.overview import totals
        from app.services.finance.domains.ledger import queries
        from app.services.finance.schemas import AccountResponse
        from tests.services._finance_factories import seed_txn

        checking = await seed_account(svc, current_balance=100_000)
        loan = await seed_account(svc, "Car Loan", "loan", "liability")
        assert checking.id is not None and loan.id is not None
        await seed_txn(svc, loan.id, 50_000, current_date())
        activity = await queries.transaction_totals_by_account(
            async_db_session, owner_user_id=1, account_ids=[checking.id, loan.id]
        )

        headline = totals(
            [
                AccountResponse.from_row(a, activity_balance=activity.get(a.id, 0))
                for a in (checking, loan)
            ],
            [],
        )

        api = await networth.get_net_worth(async_db_session, owner_user_id=1)
        assert headline["net_worth"] == api.net_worth_amount == 50_000

    @pytest.mark.asyncio
    async def test_marking_an_account_as_someone_elses_redraws_the_line(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.ledger.subjects import assign_subject

        await seed_account(svc, current_balance=100_000)
        theirs = await seed_account(
            svc, "Their Savings", "savings", current_balance=40_000
        )
        assert theirs.id is not None
        await networth.recompute_snapshots(async_db_session, owner_user_id=1)
        party = await PartyService(async_db_session).create(
            name="Line Parent", kind="person"
        )
        await async_db_session.flush()
        assert party.id is not None
        subject = await subject_for_party(
            async_db_session, party.id, name=party.name, owner_user_id=1
        )
        assert subject.id is not None

        await assign_subject(async_db_session, theirs.id, subject.id, owner_user_id=1)

        assert (await _line(async_db_session))[current_date()] == 100_000

    @pytest.mark.asyncio
    async def test_a_property_left_out_of_net_worth_redraws_the_line(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        await seed_account(svc, current_balance=100_000)
        house = await seed_account(svc, "House", "property", current_balance=30_000_000)
        assert house.id is not None
        await networth.recompute_snapshots(async_db_session, owner_user_id=1)

        await svc.set_property_details(
            house.id, owner_user_id=1, include_in_net_worth=False
        )

        assert (await _line(async_db_session))[current_date()] == 100_000

    @pytest.mark.asyncio
    async def test_a_day_nothing_counts_on_any_more_reads_zero(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The only account hidden: the window kept its $1,000, since a
        day only got written when something counted on it."""
        only = await seed_account(svc, "Savings", "savings", current_balance=100_000)
        assert only.id is not None
        await networth.recompute_snapshots(async_db_session, owner_user_id=1)

        await svc.update_account(only.id, owner_user_id=1, is_hidden=True)

        assert set((await _line(async_db_session)).values()) == {0}
