"""Tests for a payee name that OUTLIVES the transactions it was given to.

Naming a payee used to be a one-time stamp: ``assign_merchant`` set
``merchant_id`` on the rows in front of you and nothing recorded what the
bank descriptor meant. The next import arrived payee-less and offered the
same groups for naming again - on the real ledger, ShopRite among them,
already named months earlier.

The category axis has had this since the beginning
(``FinanceCategoryAlias``: free text -> canonical category, written on
create, read at import). These cover the merchant axis catching up.

An alias keys on ``transaction_payee_key`` - the same four-token
grouping the user was shown when they named it. Keying on the whole
descriptor was tried first and measured against the real ledger, where
it is close to useless: ShopRite's 407 transactions carry 211 distinct
descriptors (the card tail and date are baked into each) and only 8
prefixes, so half of next month's rows would arrive as text nobody had
ever seen.

The prefix is a heuristic, and these pin down what that costs. 5 of the
ledger's 240 named prefixes mean more than one payee - "NON CHASE ATM
WITHDRAW" covers four - so a key taught a second payee is treated as a
conflict rather than a correction, and stops resolving unattended.
"""

from datetime import date

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import FinanceMerchantAlias
from app.services.finance.service import FinanceService
from app.services.finance.utils import transaction_payee_key
from tests.services._finance_factories import seed_account as _account
from tests.services._finance_factories import seed_payee_txn as _txn

# The descriptor from the live ledger that raised this: a ShopRite the
# review reported as an unnamed, uncategorized payee.
SHOPRITE = "SHPRTE NTH RD&WNSW GT XXX-XXX-6086 NY"
# Same store, different card and day - the variance the four-token prefix
# exists to absorb.
SHOPRITE_AGAIN = "SHPRTE NTH RD&WNSW GT POUGHKEEPSIE NYXX8683 07/22"


KEY = "SHPRTE NTH RD WNSW"  # what both descriptors above group under


async def _aliases(session: AsyncSession) -> dict[str, int]:
    rows = (await session.exec(select(FinanceMerchantAlias))).all()
    return {row.normalized_alias: row.merchant_id for row in rows}


async def _ambiguous(session: AsyncSession) -> set[str]:
    rows = (await session.exec(select(FinanceMerchantAlias))).all()
    return {row.normalized_alias for row in rows if row.is_ambiguous}


class TestTheKeyAnAliasIsStoredUnder:
    def test_a_swipes_card_tail_and_date_fall_outside_the_key(self) -> None:
        """Why the whole descriptor cannot be the memory: these two are
        the same store on different days, and share only the prefix."""
        assert transaction_payee_key(None, SHOPRITE, None) == KEY
        assert transaction_payee_key(None, SHOPRITE_AGAIN, None) == KEY

    def test_the_merchant_name_wins_over_the_raw_descriptor(self) -> None:
        assert transaction_payee_key("ShopRite", SHOPRITE, "x") == "SHOPRITE"
        assert transaction_payee_key(None, None, "x") == "X"


class TestNamingRecordsWhatItMeant:
    @pytest.mark.asyncio
    async def test_assigning_a_payee_records_the_descriptor(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)

        await svc.assign_merchant([txn.id], shoprite.id, owner_user_id=1)

        assert await _aliases(async_db_session) == {KEY: shoprite.id}

    @pytest.mark.asyncio
    async def test_every_distinct_descriptor_in_the_batch_is_recorded(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Naming a GROUP is one decision, and it is remembered as one
        key - the same key the group was presented under, which is what
        a later row will arrive matching."""
        account = await _account(svc)
        first = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        second = await _txn(svc, account.id, SHOPRITE_AGAIN, date(2026, 7, 22), -3_305)
        third = await _txn(svc, account.id, SHOPRITE, date(2026, 8, 1), -1_200)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)

        await svc.assign_merchant(
            [first.id, second.id, third.id], shoprite.id, owner_user_id=1
        )

        assert await _aliases(async_db_session) == {KEY: shoprite.id}
        assert await _ambiguous(async_db_session) == set()

    @pytest.mark.asyncio
    async def test_clearing_a_payee_records_nothing(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """``merchant_id=None`` is an erasure, and an erasure has no name
        to teach."""
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)

        await svc.assign_merchant([txn.id], None, owner_user_id=1)

        assert await _aliases(async_db_session) == {}

    @pytest.mark.asyncio
    async def test_a_second_payee_for_one_key_keeps_the_newer_and_flags_it(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The newest answer is kept - the user just gave it - but the key
        is no longer trusted unattended, because being taught two payees
        is what a genuinely ambiguous descriptor looks like."""
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        wrong = await svc.create_merchant("Shell", owner_user_id=1)
        right = await svc.create_merchant("ShopRite", owner_user_id=1)

        await svc.assign_merchant([txn.id], wrong.id, owner_user_id=1)
        assert await _ambiguous(async_db_session) == set()

        await svc.assign_merchant([txn.id], right.id, owner_user_id=1)

        assert await _aliases(async_db_session) == {KEY: right.id}
        assert await _ambiguous(async_db_session) == {KEY}

    @pytest.mark.asyncio
    async def test_renaming_to_the_same_payee_is_not_a_conflict(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Re-confirming a payee must not poison its own key."""
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)

        await svc.assign_merchant([txn.id], shoprite.id, owner_user_id=1)
        await svc.assign_merchant([txn.id], shoprite.id, owner_user_id=1)

        assert await _ambiguous(async_db_session) == set()

    @pytest.mark.asyncio
    async def test_a_payee_group_assign_records_its_descriptors_too(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """``assign_payee_group`` resolves ids server-side from the keys
        and hands them to ``assign_merchant``, so it inherits the memory
        rather than needing its own."""
        account = await _account(svc)
        await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        await _txn(svc, account.id, SHOPRITE_AGAIN, date(2026, 7, 22), -3_305)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)

        assert await svc.assign_payee_group([KEY], shoprite.id, owner_user_id=1) == 2

        assert await _aliases(async_db_session) == {KEY: shoprite.id}


class TestResolvingADescriptor:
    @pytest.mark.asyncio
    async def test_a_known_descriptor_resolves_to_its_payee(
        self, svc: FinanceService
    ) -> None:
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)
        await svc.assign_merchant([txn.id], shoprite.id, owner_user_id=1)

        resolved = await svc.resolve_merchant_aliases([SHOPRITE], owner_user_id=1)

        assert resolved == {SHOPRITE: shoprite.id}

    @pytest.mark.asyncio
    async def test_a_later_swipe_of_the_same_store_resolves(
        self, svc: FinanceService
    ) -> None:
        """The case that raised the ticket: a different card and day, and
        the payee still lands."""
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)
        await svc.assign_merchant([txn.id], shoprite.id, owner_user_id=1)

        resolved = await svc.resolve_merchant_aliases([SHOPRITE_AGAIN], owner_user_id=1)

        assert resolved == {SHOPRITE_AGAIN: shoprite.id}

    @pytest.mark.asyncio
    async def test_a_key_taught_two_payees_resolves_to_nothing(
        self, svc: FinanceService
    ) -> None:
        """Better to ask than to file every ATM withdrawal under whoever
        was named last."""
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        first = await svc.create_merchant("Shell", owner_user_id=1)
        second = await svc.create_merchant("ShopRite", owner_user_id=1)
        await svc.assign_merchant([txn.id], first.id, owner_user_id=1)
        await svc.assign_merchant([txn.id], second.id, owner_user_id=1)

        assert await svc.resolve_merchant_aliases([SHOPRITE], owner_user_id=1) == {}

    @pytest.mark.asyncio
    async def test_an_unknown_descriptor_is_absent_rather_than_none(
        self, svc: FinanceService
    ) -> None:
        resolved = await svc.resolve_merchant_aliases(
            ["WHO KNOWS", "", None], owner_user_id=1
        )
        assert resolved == {}

    @pytest.mark.asyncio
    async def test_another_owners_alias_is_not_borrowed(
        self, svc: FinanceService
    ) -> None:
        account = await _account(svc)
        txn = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)
        await svc.assign_merchant([txn.id], shoprite.id, owner_user_id=1)

        assert await svc.resolve_merchant_aliases([SHOPRITE], owner_user_id=2) == {}


def _qif(payee: str) -> bytes:
    return (f"!Type:Bank\nD08/15/2026\nT-52.18\nP{payee}\nLGroceries\n^\n").encode()


class TestTheNextImportAlreadyKnows:
    @pytest.mark.asyncio
    async def test_a_named_descriptor_arrives_with_its_payee(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The whole point of the ticket: name it once, and the row that
        lands next month is already filed."""
        from app.services.finance.adapters.importers import imports
        from app.services.finance.adapters.importers.qif import parse_qif

        account = await _account(svc)
        seen = await _txn(svc, account.id, SHOPRITE, date(2026, 6, 28), -8_412)
        shoprite = await svc.create_merchant("ShopRite", owner_user_id=1)
        await svc.assign_merchant([seen.id], shoprite.id, owner_user_id=1)

        data = _qif(SHOPRITE)
        result = await imports.ingest_transactions(
            async_db_session,
            owner_user_id=1,
            source_type="qif",
            file_name="next-month.qif",
            file_bytes=data,
            parsed=parse_qif(data, source="qif"),
            default_account_id=account.id,
        )

        assert result.rows_inserted == 1
        txns, _total = await svc.list_transactions(owner_user_id=1)
        imported = next(t for t in txns if t.id != seen.id)
        assert imported.merchant_id == shoprite.id

    @pytest.mark.asyncio
    async def test_an_unknown_descriptor_still_arrives_payee_less(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """No guessing. An unrecognized descriptor goes to the naming
        backlog exactly as before."""
        from app.services.finance.adapters.importers import imports
        from app.services.finance.adapters.importers.qif import parse_qif

        account = await _account(svc)
        data = _qif("SOME PLACE NOBODY NAMED")
        await imports.ingest_transactions(
            async_db_session,
            owner_user_id=1,
            source_type="qif",
            file_name="next-month.qif",
            file_bytes=data,
            parsed=parse_qif(data, source="qif"),
            default_account_id=account.id,
        )

        txns, _total = await svc.list_transactions(owner_user_id=1)
        assert [t.merchant_id for t in txns] == [None]
