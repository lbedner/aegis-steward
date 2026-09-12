"""An institution is the ledger's address book for a bank.

It existed as a provider-only directory: a row per Plaid/SnapTrade view
of a bank, global and never user-scoped, and on a real ledger it was
empty because nothing but the Plaid path ever wrote one. Meanwhile
seventeen accounts had nowhere to record who they are with, which is
what you want when the question is "how do I reach them".

Now it is owned curation like categories, merchants and tags: NULL owner
is a provider seed, an owner id is yours. One row per bank serves every
account with it, so Fidelity's homepage is typed once and three
brokerage accounts carry it - and its domain is what the icon resolver
already turns into a logo.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account as _account


class TestOwnedLikeTheRestOfTheCuration:
    @pytest.mark.asyncio
    async def test_naming_one_keeps_it_for_that_owner(
        self, svc: FinanceService
    ) -> None:
        mine = await svc.get_or_create_institution(
            name="Fidelity", owner_user_id=1, url="https://fidelity.com"
        )
        assert mine.owner_user_id == 1
        assert mine.provider == "manual"
        assert mine.url == "https://fidelity.com"

    @pytest.mark.asyncio
    async def test_the_same_name_twice_is_one_row(self, svc: FinanceService) -> None:
        """Typed by hand, so it has to forgive case and spacing the way a
        payee does."""
        first = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        again = await svc.get_or_create_institution(name="  fidelity ", owner_user_id=1)
        assert again.id == first.id

    @pytest.mark.asyncio
    async def test_two_owners_keep_their_own(self, svc: FinanceService) -> None:
        mine = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        theirs = await svc.get_or_create_institution(name="Fidelity", owner_user_id=2)
        assert mine.id != theirs.id

    @pytest.mark.asyncio
    async def test_a_provider_seed_is_not_mine_to_reuse(
        self, svc: FinanceService
    ) -> None:
        """A NULL owner is the shared directory the connection layer
        gates on; typing a name must never edit it."""
        seed = await svc.get_or_create_institution(name="Fidelity", owner_user_id=None)
        mine = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        assert seed.owner_user_id is None
        assert mine.id != seed.id


class TestAnAccountCarriesItsInstitution:
    @pytest.mark.asyncio
    async def test_an_account_can_be_pointed_at_one(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await _account(svc)
        fidelity = await svc.get_or_create_institution(
            name="Fidelity", owner_user_id=1, url="https://fidelity.com"
        )

        updated = await svc.set_account_institution(
            account.id, fidelity.id, owner_user_id=1
        )

        assert updated is not None
        assert updated.institution_id == fidelity.id

    @pytest.mark.asyncio
    async def test_it_can_be_taken_off_again(self, svc: FinanceService) -> None:
        account = await _account(svc)
        fidelity = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        await svc.set_account_institution(account.id, fidelity.id, owner_user_id=1)

        cleared = await svc.set_account_institution(account.id, None, owner_user_id=1)

        assert cleared is not None
        assert cleared.institution_id is None


class TestWhatThePickerOffers:
    """Nobody should have to "create an institution" as an errand. The
    field offers what you have, suggests what your ledger already knows,
    and creates whatever you type."""

    @pytest.mark.asyncio
    async def test_it_lists_the_ones_you_have(self, svc: FinanceService) -> None:
        await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        await svc.get_or_create_institution(name="Chase", owner_user_id=1)
        await svc.get_or_create_institution(name="Someone else's", owner_user_id=2)

        mine = await svc.list_institutions(owner_user_id=1)

        assert [i.name for i in mine] == ["Chase", "Fidelity"]

    @pytest.mark.asyncio
    async def test_the_last_one_used_is_remembered(self, svc: FinanceService) -> None:
        """Three brokerage accounts at one bank: name it once and the next
        account opens on it. Read from the accounts themselves rather than
        stored, so it cannot go stale."""
        chase = await svc.get_or_create_institution(name="Chase", owner_user_id=1)
        fidelity = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        first = await _account(svc, name="Checking")
        second = await _account(svc, name="ROTH IRA")
        await svc.set_account_institution(first.id, chase.id, owner_user_id=1)
        await svc.set_account_institution(second.id, fidelity.id, owner_user_id=1)

        assert await svc.last_institution_used(owner_user_id=1) == fidelity.id

    @pytest.mark.asyncio
    async def test_nothing_used_yet_remembers_nothing(
        self, svc: FinanceService
    ) -> None:
        await _account(svc)
        assert await svc.last_institution_used(owner_user_id=1) is None


class TestWhatTheDirectoryShows:
    @pytest.mark.asyncio
    async def test_each_bank_counts_the_accounts_behind_it(
        self, svc: FinanceService
    ) -> None:
        """A duplicate is visible rather than discovered later, and a
        bank nothing uses is safe to delete."""
        fidelity = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        await svc.get_or_create_institution(name="Chase", owner_user_id=1)
        for name in ("ROTH IRA", "Traditional IRA"):
            account = await _account(svc, name=name)
            await svc.set_account_institution(account.id, fidelity.id, owner_user_id=1)

        rows = await svc.institution_usage(owner_user_id=1)

        assert {r.name: r.account_count for r in rows} == {"Chase": 0, "Fidelity": 2}

    @pytest.mark.asyncio
    async def test_details_can_be_edited(self, svc: FinanceService) -> None:
        bank = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)

        updated = await svc.update_institution(
            bank.id,
            name="Fidelity Investments",
            url="https://fidelity.com",
            phone="800-343-3548",
            owner_user_id=1,
        )

        assert updated is not None
        assert updated.name == "Fidelity Investments"
        assert updated.url == "https://fidelity.com"
        assert updated.metadata_["phone"] == "800-343-3548"
        # The homepage is where the logo comes from, so it is derived
        # rather than asked for twice.
        assert updated.domain == "fidelity.com"

    @pytest.mark.asyncio
    async def test_renaming_keeps_the_dedup_key_honest(
        self, svc: FinanceService
    ) -> None:
        bank = await svc.get_or_create_institution(name="Fidelity", owner_user_id=1)
        await svc.update_institution(
            bank.id, name="Fidelity Investments", owner_user_id=1
        )

        again = await svc.get_or_create_institution(
            name="fidelity investments", owner_user_id=1
        )
        assert again.id == bank.id
