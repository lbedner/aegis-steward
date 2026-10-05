"""One account where there were two (#309): the Quicken-fed checking and
the copy a bank link made beside it, merged - previewed first.

The account you merge INTO keeps its name; the copy's rows move over, its
bank link with them, and a charge both brought is kept once.
"""

from datetime import date

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import merging
from app.services.finance.domains.ledger.queries.purge import ACCOUNT_COLUMNS
from app.services.finance.models import (
    FinanceAccount,
    FinanceConnection,
    FinanceValuation,
)
from app.services.finance.service import FinanceService
from tests.services.test_finance_plaid import _ACCOUNTS, _connect
from tests.services.test_two_feeds import _ONCE, _import, _sync, _visible


async def _doubled(db: AsyncSession) -> tuple[FinanceAccount, FinanceAccount]:
    """The bank linked before #309 could attach it: its own copy, and the
    Quicken export imported into the account you had."""
    connection = await _connect(db)
    await _sync(db, connection)  # nothing of yours yet: its own account
    (copy,) = (await FinanceService(db).list_accounts(owner_user_id=1))[0]
    yours = await FinanceService(db).create_manual_account(
        owner_user_id=1,
        name="Checking",
        account_type="checking",
        classification="asset",
    )
    await _import(db, yours)
    return yours, await db.get(FinanceAccount, copy.id)


# A sync, an import, then the merge: each reads the feeds' rows once.
@pytest.mark.queryspy(threshold=4)
class TestTheDoubledAccount:
    @pytest.mark.asyncio
    async def test_the_preview_says_what_a_merge_would_do_and_does_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        yours, copy = await _doubled(async_db_session)

        plan = await merging.plan(async_db_session, yours, copy)

        assert plan.refusal is None
        assert (plan.moving, plan.same_charges, plan.link_moves) == (6, 4, True)
        assert len(await _visible(async_db_session)) == 10  # still doubled

    @pytest.mark.asyncio
    async def test_a_merge_leaves_one_account_each_charge_once_still_linked(
        self, async_db_session: AsyncSession
    ) -> None:
        yours, copy = await _doubled(async_db_session)
        link = copy.connection_id

        await merging.merge(async_db_session, yours, copy)

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        assert [(a.id, a.name, a.connection_id) for a in accounts] == [
            (yours.id, "Checking", link)
        ]
        assert await _visible(async_db_session) == _ONCE

    @pytest.mark.asyncio
    async def test_the_next_sync_lands_on_the_merged_account(
        self, async_db_session: AsyncSession
    ) -> None:
        yours, copy = await _doubled(async_db_session)
        connection = await async_db_session.get(FinanceConnection, copy.connection_id)
        assert connection is not None
        await merging.merge(async_db_session, yours, copy)

        connection.sync_cursor = None  # the bank sends it all again
        await _sync(async_db_session, connection)

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        assert [a.id for a in accounts] == [yours.id]
        assert await _visible(async_db_session) == _ONCE

    @pytest.mark.asyncio
    async def test_where_both_have_a_days_value_yours_is_kept(
        self, async_db_session: AsyncSession
    ) -> None:
        yours, copy = await _doubled(async_db_session)
        day = date(2026, 9, 30)
        for account, value in ((yours, 100), (copy, 999)):
            async_db_session.add(
                FinanceValuation(
                    account_id=account.id, as_of_date=day, value=value, source="manual"
                )
            )
        await async_db_session.flush()

        await merging.merge(async_db_session, yours, copy)

        kept = (
            await async_db_session.exec(
                select(FinanceValuation.value).where(FinanceValuation.as_of_date == day)
            )
        ).all()
        assert kept == [100]


# A sync, an import, then the merge: each reads the feeds' rows once.
@pytest.mark.queryspy(threshold=4)
class TestWhatTheBothHad:
    @pytest.mark.asyncio
    async def test_a_stream_detected_on_both_becomes_yours(
        self, async_db_session: AsyncSession
    ) -> None:
        """Detection found the same subscription on each copy; one account
        has one stream per payee, so the copy's folds into yours."""
        from app.services.finance.models import (
            FinanceRecurringStream,
            FinanceTransaction,
        )

        yours, copy = await _doubled(async_db_session)
        streams = {}
        for account in (yours, copy):
            stream = FinanceRecurringStream(
                owner_user_id=1,
                account_id=account.id,
                name="Netflix",
                normalized_payee="netflix",
                direction="outflow",
                frequency="monthly",
                source="derived",
            )
            async_db_session.add(stream)
            await async_db_session.flush()
            streams[account.id] = stream.id
        charge = (
            await async_db_session.exec(
                select(FinanceTransaction).where(
                    FinanceTransaction.account_id == copy.id
                )
            )
        ).first()
        charge.recurring_stream_id = streams[copy.id]
        async_db_session.add(charge)
        await async_db_session.flush()

        await merging.merge(async_db_session, yours, copy)

        left = (await async_db_session.exec(select(FinanceRecurringStream.id))).all()
        assert left == [streams[yours.id]]
        await async_db_session.refresh(charge)
        assert charge.recurring_stream_id == streams[yours.id]


class TestWhatCannotMerge:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two links, each finds its bank
    async def test_two_linked_accounts_are_two_banks_feeds(
        self, async_db_session: AsyncSession
    ) -> None:
        first = await _connect(async_db_session, "item-1")
        second = await _connect(async_db_session, "item-2", "tok2")
        await _sync(async_db_session, first)
        await _sync(async_db_session, second, accounts=[_ACCOUNTS[1]])
        one, other = (
            await FinanceService(async_db_session).list_accounts(owner_user_id=1)
        )[0]

        plan = await merging.plan(async_db_session, one, other)

        assert plan.refusal

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a sync, then the import
    async def test_yours_does_not_fold_into_the_banks_copy(
        self, async_db_session: AsyncSession
    ) -> None:
        """The copy goes into yours, not the other way: your rows were
        filed for your account, and a later import knows them by it."""
        yours, copy = await _doubled(async_db_session)

        assert (await merging.plan(async_db_session, copy, yours)).refusal
        assert (await merging.merge(async_db_session, copy, yours)).refusal
        assert yours.deleted_at is None

    @pytest.mark.asyncio
    async def test_two_of_yours_are_not_a_bank_and_its_copy(
        self, async_db_session: AsyncSession
    ) -> None:
        service = FinanceService(async_db_session)
        one = await service.create_manual_account(
            owner_user_id=1, name="One", account_type="checking", classification="asset"
        )
        two = await service.create_manual_account(
            owner_user_id=1, name="Two", account_type="checking", classification="asset"
        )

        assert (await merging.plan(async_db_session, one, two)).refusal

    @pytest.mark.asyncio
    async def test_money_owed_is_not_money_held(
        self, async_db_session: AsyncSession
    ) -> None:
        service = FinanceService(async_db_session)
        cash = await service.create_manual_account(
            owner_user_id=1, name="Cash", account_type="cash", classification="asset"
        )
        card = await service.create_manual_account(
            owner_user_id=1,
            name="Card",
            account_type="credit_card",
            classification="liability",
        )

        assert (await merging.plan(async_db_session, cash, card)).refusal


def test_every_reference_to_an_account_is_one_a_merge_moves() -> None:
    """A table added later that points at accounts, and left off the list,
    would keep rows on the account a merge removes."""
    from sqlmodel import SQLModel

    from app.core.model_registry import import_all_models

    import_all_models()
    references = {
        (fk.parent.table.name, fk.parent.name)
        for table in SQLModel.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "finance_account"
    }
    listed = {(c.class_.__tablename__, c.key) for c in ACCOUNT_COLUMNS}
    assert references <= listed, sorted(references - listed)
