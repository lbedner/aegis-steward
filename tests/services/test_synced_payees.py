"""A synced row is named and filed by your own history (#324).

SimpleFIN's Chase rows arrived with no payee and no category: a synced
row took only the provider's category, and the payee memory had been
taught Quicken's renamed payees ("Fairacre"), never the bank's wording
("ADAMS FAIRACRE FARMS 845-454-4330 NY"). Every charge both feeds bring
now teaches the memory that wording, and a synced row is named by it -
else by the provider's own name for it, when that is one of your payees
- and filed where the payee is filed. Nothing is guessed: a payee whose
rows split across categories leaves the row for you.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import connections
from app.services.finance.domains.ledger import payee_aliases
from app.services.finance.models import (
    FinanceAccount,
    FinanceCategory,
    FinanceConnection,
    FinanceMerchant,
    FinanceTransaction,
)
from app.services.finance.service import FinanceService
from tests.services.test_finance_plaid import _ACCOUNTS, FakePlaidClient, _connect
from tests.services.test_two_feeds import _quicken, _rows, _sync, _txn

BANK_WORDING = "ADAMS FAIRACRE FARMS 845-454-4330 NY 09/02"


async def _category(db: AsyncSession, name: str) -> FinanceCategory:
    category = FinanceCategory(slug=name.lower(), name=name, classification="expense")
    db.add(category)
    await db.flush()
    return category


async def _payee(
    db: AsyncSession, name: str, default: FinanceCategory | None = None
) -> FinanceMerchant:
    payee = await FinanceService(db).create_merchant(name, owner_user_id=1)
    payee.default_category_id = default.id if default else None
    db.add(payee)
    await db.flush()
    return payee


async def _filed(
    db: AsyncSession,
    account: FinanceAccount,
    payee: FinanceMerchant,
    category: FinanceCategory | None,
    day: int,
    cents: int = -1000,
) -> FinanceTransaction:
    """A row your export brought, its payee and category curated."""
    assert account.id is not None
    return await FinanceService(db).create_transaction(
        owner_user_id=1,
        account_id=account.id,
        amount=cents,
        txn_date=date(2026, 8, day),
        name=payee.name,
        source="qif",
        merchant_id=payee.id,
        category_id=category.id if category else None,
        category_source="user" if category else "unset",
    )


async def _linked(db: AsyncSession) -> tuple[FinanceAccount, FinanceConnection]:
    """Your Quicken-fed checking, and its bank linked beside it."""
    return await _quicken(db), await _connect(db)


class TestAPairTeaches:
    @pytest.mark.asyncio
    async def test_the_banks_wording_is_learned_from_a_charge_both_bring(
        self, async_db_session: AsyncSession
    ) -> None:
        account, connection = await _linked(async_db_session)
        farm = await _payee(async_db_session, "Fairacre")
        exported = await _filed(async_db_session, account, farm, None, day=1)
        exported.date_ = date(2026, 9, 1)
        async_db_session.add(exported)

        await _sync(
            async_db_session, connection, [_txn("p_farm", "02", 10.0, BANK_WORDING)]
        )

        assert await payee_aliases.resolve_merchant_aliases(
            async_db_session,
            ["ADAMS FAIRACRE FARMS 845-555-0000 NY 10/03"],
            owner_user_id=1,
        ) == {"ADAMS FAIRACRE FARMS 845-555-0000 NY 10/03": farm.id}

    @pytest.mark.asyncio
    async def test_a_rebuilt_memory_keeps_what_pairs_taught(
        self, async_db_session: AsyncSession
    ) -> None:
        """The memory answers to the ledger: rebuilt, it reads the pairs
        again rather than forgetting the bank's wording."""
        account, connection = await _linked(async_db_session)
        farm = await _payee(async_db_session, "Fairacre")
        exported = await _filed(async_db_session, account, farm, None, day=1)
        exported.date_ = date(2026, 9, 1)
        async_db_session.add(exported)
        await _sync(
            async_db_session, connection, [_txn("p_farm", "02", 10.0, BANK_WORDING)]
        )

        await payee_aliases.recompute_payee_aliases(async_db_session, owner_user_id=1)

        assert await payee_aliases.resolve_merchant_aliases(
            async_db_session, [BANK_WORDING], owner_user_id=1
        ) == {BANK_WORDING: farm.id}


class TestASyncedRow:
    @pytest.mark.asyncio
    async def test_is_named_by_the_memory_and_filed_by_the_payees_default(
        self, async_db_session: AsyncSession
    ) -> None:
        _account, connection = await _linked(async_db_session)
        groceries = await _category(async_db_session, "Groceries")
        farm = await _payee(async_db_session, "Fairacre", default=groceries)
        await payee_aliases.remember_payee_keys(
            async_db_session,
            [
                FinanceTransaction(
                    account_id=0,
                    amount=0,
                    date_=date(2026, 9, 1),
                    name=BANK_WORDING,
                    source="plaid",
                )
            ],
            farm.id,
            owner_user_id=1,
        )

        await _sync(
            async_db_session, connection, [_txn("p_farm", "10", 10.0, BANK_WORDING)]
        )

        row = (await _rows(async_db_session))["p_farm"]
        assert (row.merchant_id, row.category_id) == (farm.id, groceries.id)
        assert row.category_source == "rule"

    @pytest.mark.asyncio
    async def test_is_named_by_the_providers_name_when_it_is_one_of_yours(
        self, async_db_session: AsyncSession
    ) -> None:
        """The bank called it Starbucks; you have a payee called that."""
        account, connection = await _linked(async_db_session)
        coffee = await _category(async_db_session, "Coffee")
        starbucks = await _payee(async_db_session, "Starbucks")
        for day in (1, 2, 3):
            await _filed(async_db_session, account, starbucks, coffee, day=day)

        await _sync(
            async_db_session,
            connection,
            [
                _txn(
                    "p_sb",
                    "10",
                    5.0,
                    "POS DEBIT STARBUCKS 8007827282",
                    merchant_name="Starbucks",
                )
            ],
        )

        row = (await _rows(async_db_session))["p_sb"]
        assert (row.merchant_id, row.category_id) == (starbucks.id, coffee.id)

    @pytest.mark.asyncio
    async def test_a_payee_filed_two_ways_leaves_the_category_to_you(
        self, async_db_session: AsyncSession
    ) -> None:
        """Target is groceries one week and household the next: the row is
        named, but not filed on a guess."""
        account, connection = await _linked(async_db_session)
        groceries = await _category(async_db_session, "Groceries")
        household = await _category(async_db_session, "Household")
        target = await _payee(async_db_session, "Target")
        for day, category in (
            (1, groceries),
            (2, groceries),
            (3, household),
            (4, household),
        ):
            await _filed(async_db_session, account, target, category, day=day)

        await _sync(
            async_db_session,
            connection,
            [
                _txn(
                    "p_tgt",
                    "10",
                    30.0,
                    "TARGET 00012345",
                    merchant_name="Target",
                    personal_finance_category={},  # SimpleFIN sends none
                )
            ],
        )

        row = (await _rows(async_db_session))["p_tgt"]
        assert row.merchant_id == target.id
        assert row.category_id is None
        assert row.category_source == "unset"

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_row_stored_unnamed_is_named_when_it_syncs_again(
        self, async_db_session: AsyncSession
    ) -> None:
        """Chase's first rows came in before the memory knew the wording:
        the next sync names and files them, rather than leaving them."""
        _account, connection = await _linked(async_db_session)
        groceries = await _category(async_db_session, "Groceries")
        farm = await _payee(async_db_session, "Fairacre", default=groceries)
        unfiled = [
            _txn("p_farm", "10", 10.0, BANK_WORDING, personal_finance_category={})
        ]
        await _sync(async_db_session, connection, unfiled)
        assert (await _rows(async_db_session))["p_farm"].merchant_id is None
        assert farm.id is not None
        await payee_aliases.remember_payee_keys(
            async_db_session,
            [
                FinanceTransaction(
                    account_id=0,
                    amount=0,
                    date_=date(2026, 9, 1),
                    name=BANK_WORDING,
                    source="plaid",
                )
            ],
            farm.id,
            owner_user_id=1,
        )

        # SimpleFIN sends its whole window each time; a fake Plaid that
        # answers every pull, cursor or not, does the same.
        await connections.sync_plaid_connection(
            async_db_session,
            connection,
            client=FakePlaidClient(_ACCOUNTS[:1], unfiled, always=True),
        )

        row = (await _rows(async_db_session))["p_farm"]
        assert (row.merchant_id, row.category_id) == (farm.id, groceries.id)
