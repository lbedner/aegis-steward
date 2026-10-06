"""One account, two feeds (#309): a Quicken export and a Plaid sync of the
same checking account, in either order, land each charge once.

``two_feeds_checking.qif`` is the export; ``_PLAID`` is the same month as
Plaid reports it - two charges dated a day later than the export dates
them, one Plaid-only charge, and one still pending.
"""

from pathlib import Path

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.importers import imports
from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.connections import placing
from app.services.finance.models import (
    FinanceAccount,
    FinanceConnection,
    FinanceTransaction,
)
from app.services.finance.service import FinanceService
from tests.services.test_finance_plaid import _ACCOUNTS, FakePlaidClient, _connect

_QIF = (
    Path(__file__).parent / "finance" / "fixtures" / "two_feeds_checking.qif"
).read_bytes()


def _txn(txn_id: str, day: str, amount: float, name: str, **extra) -> dict:
    """Plaid's shape: positive is money out."""
    return {
        "transaction_id": txn_id,
        "account_id": "acc_check",
        "amount": amount,
        "date": f"2026-09-{day}",
        "name": name,
        "iso_currency_code": "USD",
        "pending": False,
        "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"},
        **extra,
    }


_PLAID = [
    _txn("p_mcd", "02", 12.0, "MCDONALDS 3322"),  # a day after the export
    _txn("p_bb1", "02", 5.0, "BLUE BOTTLE"),
    _txn("p_bb2", "04", 5.0, "BLUE BOTTLE"),  # a day after the export
    _txn("p_pay", "05", -2500.0, "ACME PAYROLL"),
    _txn("p_uber", "06", 20.0, "UBER"),  # only Plaid has it
    _txn("p_tea", "07", 7.5, "TEA HOUSE", pending=True),
]


async def _quicken(db: AsyncSession, mask: str | None = "0000") -> FinanceAccount:
    """The Quicken-fed checking account, as an import made it. ``mask`` is
    its last four, there when somebody entered the account number."""
    account = await FinanceService(db).create_manual_account(
        owner_user_id=1,
        name="Checking",
        account_type="checking",
        classification="asset",
    )
    account.mask = mask
    db.add(account)
    await db.flush()
    return account


async def _checking(db: AsyncSession) -> tuple[FinanceAccount, FinanceConnection]:
    """The Quicken-fed checking account and its bank, about to be linked:
    the last four match, so the link attaches to it on its own."""
    return await _quicken(db), await _connect(db)


async def _import(db: AsyncSession, account: FinanceAccount):
    return await imports.import_file(
        db, owner_user_id=1, file_name="q.qif", file_bytes=_QIF, account_id=account.id
    )


async def _sync(
    db: AsyncSession,
    connection: FinanceConnection,
    txns: list[dict] = _PLAID,
    accounts: list[dict] = _ACCOUNTS[:1],
):
    return await connections.sync_plaid_connection(
        db, connection, client=FakePlaidClient(accounts, txns)
    )


async def _visible(db: AsyncSession) -> list[tuple[str, int, str]]:
    """What every total counts: (date, cents, name), file and feed alike."""
    rows, _total = await FinanceService(db).list_transactions(
        owner_user_id=1, page_size=100
    )
    return sorted((str(t.date_), t.amount, t.name or "") for t in rows)


async def _rows(db: AsyncSession) -> dict[str, FinanceTransaction]:
    found = await db.exec(
        select(FinanceTransaction).where(col(FinanceTransaction.deleted_at).is_(None))
    )
    return {(t.external_id or t.name or ""): t for t in found.all()}


_ONCE = [
    ("2026-09-01", -1200, "McDonald's"),
    ("2026-09-02", -500, "Blue Bottle Coffee"),
    ("2026-09-03", -500, "Blue Bottle Coffee"),
    ("2026-09-05", 250000, "Acme Payroll"),
    ("2026-09-06", -2000, "UBER"),
    ("2026-09-07", -750, "TEA HOUSE"),
]


class TestTheExportFirst:
    @pytest.mark.asyncio
    async def test_a_sync_lands_each_charge_once(
        self, async_db_session: AsyncSession
    ) -> None:
        account, connection = await _checking(async_db_session)
        await _import(async_db_session, account)
        await _sync(async_db_session, connection)

        assert await _visible(async_db_session) == _ONCE
        rows = await _rows(async_db_session)
        # Both kept: the bank's row so its later edits still find it, the
        # export's as the one every total reads.
        assert rows["p_mcd"].dedup_status == "duplicate"
        assert rows["p_mcd"].canonical_transaction_id == rows["McDonald's"].id
        assert rows["McDonald's"].dedup_status == "primary"
        assert rows["p_uber"].dedup_status == "unique"
        # The register reconciles against one copy of each charge too.
        from datetime import date

        from app.services.finance.domains.ledger.queries.accounts import (
            register_balance_through,
        )

        assert account.id is not None
        assert (
            await register_balance_through(
                async_db_session, account.id, date(2026, 9, 30)
            )
            == -1200 - 500 - 500 + 250000 - 2000
        )

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_resync_changes_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        account, connection = await _checking(async_db_session)
        await _import(async_db_session, account)
        await _sync(async_db_session, connection)
        connection.sync_cursor = None  # Plaid sends it all again
        await _sync(async_db_session, connection)

        assert await _visible(async_db_session) == _ONCE

    @pytest.mark.asyncio
    async def test_a_charge_days_apart_is_two_charges(
        self, async_db_session: AsyncSession
    ) -> None:
        """Equal money a week apart is a second charge, not drift."""
        account, connection = await _checking(async_db_session)
        await _import(async_db_session, account)
        await _sync(async_db_session, connection, [_txn("p_late", "08", 12.0, "MCD")])

        assert ("2026-09-08", -1200, "MCD") in await _visible(async_db_session)


class TestTheFeedFirst:
    @pytest.mark.asyncio
    async def test_an_import_lands_each_charge_once_and_wins(
        self, async_db_session: AsyncSession
    ) -> None:
        """The export carries the curated payees and categories: its row is
        the one kept in view, even arriving second."""
        account, connection = await _checking(async_db_session)
        await _sync(async_db_session, connection)
        result = await _import(async_db_session, account)

        assert result.rows_inserted == 4
        assert await _visible(async_db_session) == _ONCE
        rows = await _rows(async_db_session)
        assert rows["p_pay"].dedup_status == "duplicate"
        assert rows["p_pay"].canonical_transaction_id == rows["Acme Payroll"].id
        assert rows["Acme Payroll"].dedup_status == "primary"

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # its rows, before and after
    async def test_a_category_set_by_hand_on_the_feed_row_is_kept(
        self, async_db_session: AsyncSession
    ) -> None:
        account, connection = await _checking(async_db_session)
        await _sync(async_db_session, connection)
        service = FinanceService(async_db_session)
        treats = await service.get_or_create_category_from_hint("Treats")
        mcd = (await _rows(async_db_session))["p_mcd"]
        mcd.category_id, mcd.category_source = treats.id, "user"
        async_db_session.add(mcd)
        await async_db_session.flush()

        await _import(async_db_session, account)

        kept = (await _rows(async_db_session))["McDonald's"]
        assert (kept.category_id, kept.category_source) == (treats.id, "user")


class TestLinkingTheBank:
    """A link lands on the account an export already feeds, never beside
    it: on its own when the last four match, else by your choice."""

    @pytest.mark.asyncio
    async def test_the_same_last_four_attaches_on_its_own(
        self, async_db_session: AsyncSession
    ) -> None:
        account, connection = await _checking(async_db_session)
        await _sync(async_db_session, connection)

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        # Your name for it stays: a provider names only what it makes.
        assert [(a.id, a.name, a.connection_id) for a in accounts] == [
            (account.id, "Checking", connection.id)
        ]

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # links, then places
    async def test_an_unclear_account_waits_for_your_choice(
        self, async_db_session: AsyncSession
    ) -> None:
        """No last four to go on: the bank's account is held, its rows with
        it, until you say which account it is - then its history arrives
        and pairs with the export's."""
        account = await _quicken(async_db_session, mask=None)
        await _import(async_db_session, account)
        connection = await _connect(async_db_session)
        await _sync(async_db_session, connection)

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        assert [(a.id, a.connection_id) for a in accounts] == [(account.id, None)]
        assert [held["id"] for held in placing.unplaced(connection)] == ["acc_check"]
        assert len(await _visible(async_db_session)) == 4  # the export's alone

        assert not await placing.place(
            async_db_session, connection, {"acc_check": str(account.id)}
        )
        await _sync(async_db_session, connection)

        assert await _visible(async_db_session) == _ONCE
        assert placing.unplaced(connection) == []

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # links, then places
    async def test_a_held_account_can_be_its_own(
        self, async_db_session: AsyncSession
    ) -> None:
        account = await _quicken(async_db_session, mask=None)
        await _import(async_db_session, account)
        connection = await _connect(async_db_session)
        await _sync(async_db_session, connection)

        assert not await placing.place(
            async_db_session, connection, {"acc_check": placing.OWN}
        )
        await _sync(async_db_session, connection)

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        assert sorted((a.name, a.connection_id) for a in accounts) == [
            ("Checking", None),
            ("Plaid Checking", connection.id),
        ]

    @pytest.mark.asyncio
    async def test_an_exact_match_is_taken_before_anything_is_held(
        self, async_db_session: AsyncSession
    ) -> None:
        """The savings, listed first, could have been your checking - until
        the checking, listed second, claims it by its last four. Nothing is
        left for the savings to be, so it is its own; nothing waits."""
        account = await _quicken(async_db_session)
        connection = await _connect(async_db_session)
        unclear = {**_ACCOUNTS[1], "mask": None}
        await _sync(async_db_session, connection, [], [unclear, _ACCOUNTS[0]])

        accounts, _ = await FinanceService(async_db_session).list_accounts(
            owner_user_id=1
        )
        assert sorted((a.name, a.id == account.id) for a in accounts) == [
            ("Checking", True),
            ("Plaid Saving", False),
        ]
        assert placing.unplaced(connection) == []

    @pytest.mark.asyncio
    async def test_a_brokerage_is_not_asked_about_a_checking_account(
        self, async_db_session: AsyncSession
    ) -> None:
        """Both are assets, but cash is not investments: an IRA could never
        be your checking account, so it is not held to ask."""
        await _quicken(async_db_session, mask=None)
        connection = await _connect(async_db_session)
        await _sync(async_db_session, connection, [], [_ACCOUNTS[3]])

        assert placing.unplaced(connection) == []

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # links, then places
    async def test_a_place_with_no_answer_is_refused_and_changes_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        await _quicken(async_db_session, mask=None)
        connection = await _connect(async_db_session)
        await _sync(async_db_session, connection)

        assert await placing.place(async_db_session, connection, {})
        assert [held["id"] for held in placing.unplaced(connection)] == ["acc_check"]

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # links, then places
    async def test_a_skipped_account_waits_while_the_rest_are_placed(
        self, async_db_session: AsyncSession
    ) -> None:
        """Skip for now: nine steps are safe to start when one you are not
        sure of can wait on the card for another day."""
        account = await _quicken(async_db_session, mask=None)
        connection = await _connect(async_db_session)
        unclear = {**_ACCOUNTS[1], "mask": None}
        await _sync(async_db_session, connection, [], [_ACCOUNTS[0], unclear])
        assert len(placing.unplaced(connection)) == 2

        assert not await placing.place(
            async_db_session,
            connection,
            {"acc_check": str(account.id), "acc_savings": placing.SKIP},
        )

        assert [held["id"] for held in placing.unplaced(connection)] == ["acc_savings"]
        await async_db_session.refresh(account)
        assert account.connection_id == connection.id


class TestPairingAtScale:
    @pytest.mark.asyncio
    async def test_pairs_are_made_a_round_at_a_time(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Years of charges pair in rounds, never one read of every id."""
        from app.services.finance.domains.ledger import two_feeds

        monkeypatch.setattr(two_feeds, "PAIRS_PER_ROUND", 1)
        account, connection = await _checking(async_db_session)
        await _import(async_db_session, account)
        await _sync(async_db_session, connection)

        assert await _visible(async_db_session) == _ONCE


class TestTheFeedPostsLater:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_bank_row_that_posts_later_pairs_then(
        self, async_db_session: AsyncSession
    ) -> None:
        """Pending when the export arrived, so it waited; the same row
        posting later is the same charge, and pairs then."""
        account, connection = await _checking(async_db_session)
        await _sync(async_db_session, connection, [_PLAID[0] | {"pending": True}])
        await _import(async_db_session, account)

        connection.sync_cursor = None
        await _sync(async_db_session, connection, [_PLAID[0]])

        assert len(await _visible(async_db_session)) == 4  # the export's alone
