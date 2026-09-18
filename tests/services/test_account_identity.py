"""An account says which one it is, and who holds it.

Three shapes, decided together. The bank is an ORGANIZATION - a contact
behind an institution - so a routing number lives there, because every
account at that bank shares it. The account carries its own number, and
that one is kept the way a password is: with the routing number beside
it, an account number is enough for somebody to pull a debit.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger.numbers import (
    aba_ok,
    reveal_number,
    set_number,
)
from app.services.finance.service import FinanceService
from app.services.matters.service import PartyService
from tests.services._finance_factories import seed_account


class TestARoutingNumberIsCheckable:
    """Nine digits with a weighted checksum, so a transposed pair is
    caught for free rather than filed and trusted."""

    def test_a_real_one_passes(self) -> None:
        assert aba_ok("021000021")  # JPMorgan Chase, New York
        assert aba_ok("011401533")

    def test_a_transposed_pair_fails(self) -> None:
        assert not aba_ok("020100021")

    def test_the_wrong_length_or_not_digits(self) -> None:
        assert not aba_ok("02100002")
        assert not aba_ok("0210000210")
        assert not aba_ok("02100002x")
        assert not aba_ok("")

    def test_spacing_and_dashes_do_not_decide_it(self) -> None:
        assert aba_ok("021-000 021")


class TestTheNumberIsKeptLikeAPassword:
    @pytest.mark.asyncio
    async def test_it_round_trips_and_the_mask_is_derived(
        self, async_db_session: AsyncSession
    ) -> None:
        account = await seed_account(FinanceService(async_db_session), name="Checking")

        await set_number(async_db_session, int(account.id), "1234567894419")
        await async_db_session.commit()

        assert account.mask == "4419"
        assert await reveal_number(async_db_session, int(account.id)) == "1234567894419"

    @pytest.mark.asyncio
    async def test_the_plaintext_is_not_what_is_stored(
        self, async_db_session: AsyncSession
    ) -> None:
        account = await seed_account(FinanceService(async_db_session), name="Secret")

        await set_number(async_db_session, int(account.id), "1234567894419")
        await async_db_session.flush()

        assert "1234567894419" not in (account.account_number_encrypted or "")

    @pytest.mark.asyncio
    async def test_a_blank_leaves_the_stored_number_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        """It can never mean "clear it": the form never showed the old
        one, so an empty box is a field nobody touched."""
        account = await seed_account(FinanceService(async_db_session), name="Blank")
        await set_number(async_db_session, int(account.id), "1234567894419")

        await set_number(async_db_session, int(account.id), "   ")
        await async_db_session.flush()

        assert await reveal_number(async_db_session, int(account.id)) == "1234567894419"
        assert account.mask == "4419"

    @pytest.mark.asyncio
    async def test_an_account_with_no_number_reveals_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        account = await seed_account(FinanceService(async_db_session), name="Empty")

        assert await reveal_number(async_db_session, int(account.id)) is None

    def test_no_listing_can_carry_it(self) -> None:
        """The response a page and an agent both read has no field for
        it, so it cannot leak by being serialized somewhere new."""
        from app.services.finance.schemas.accounts import AccountResponse

        fields = set(AccountResponse.model_fields)
        assert "account_number_encrypted" not in fields
        assert "account_number" not in fields


class TestPointingAnAccountAtItsBank:
    async def _chase(self, db: AsyncSession) -> tuple[int, int]:
        party = await PartyService(db).create(
            name="JPMorgan Chase", kind="organization", contact={"website": "chase.com"}
        )
        account = await seed_account(FinanceService(db), name="TOTAL CHECKING (CHASE)")
        await db.flush()
        return int(party.id), int(account.id)

    @pytest.mark.asyncio
    async def test_the_card_names_the_contact_and_the_account(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.accounts import (
            InstitutionPayload,
            institution_describe,
        )

        party_id, account_id = await self._chase(async_db_session)
        said = {
            r.label: r.value
            for r in await institution_describe(
                async_db_session,
                InstitutionPayload(account_id=account_id, party_id=party_id),
                None,
            )
        }

        assert said["Account"] == "TOTAL CHECKING (CHASE)"
        assert said["Held with"].endswith("JPMorgan Chase")

    @pytest.mark.asyncio
    async def test_approving_it_points_the_account_at_the_bank(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.ledger.subjects import institution_of
        from app.services.finance.domains.writes.accounts import (
            InstitutionPayload,
            institution_execute,
        )

        party_id, account_id = await self._chase(async_db_session)

        await institution_execute(
            async_db_session,
            InstitutionPayload(account_id=account_id, party_id=party_id),
            None,
        )
        await async_db_session.commit()

        account = await FinanceService(async_db_session).get_account(account_id)
        held = await institution_of(async_db_session, party_id)
        assert account.institution_id == held.id

    @pytest.mark.asyncio
    async def test_a_second_account_reuses_the_one_bank(
        self, async_db_session: AsyncSession
    ) -> None:
        """Two accounts at one bank is one institution. Naming it twice
        is how a website lands on one row and a logo on the other."""
        from app.services.finance.domains.writes.accounts import (
            InstitutionPayload,
            institution_execute,
        )

        party_id, first = await self._chase(async_db_session)
        second = await seed_account(FinanceService(async_db_session), name="SAVINGS")
        await async_db_session.flush()

        for account_id in (first, int(second.id)):
            await institution_execute(
                async_db_session,
                InstitutionPayload(account_id=account_id, party_id=party_id),
                None,
            )
        await async_db_session.commit()

        service = FinanceService(async_db_session)
        held = {
            (await service.get_account(one)).institution_id
            for one in (first, int(second.id))
        }
        assert len(held) == 1

    @pytest.mark.asyncio
    async def test_the_routing_number_rides_with_the_bank(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.ledger.subjects import institution_of
        from app.services.finance.domains.writes.accounts import (
            InstitutionPayload,
            institution_execute,
        )

        party_id, account_id = await self._chase(async_db_session)

        await institution_execute(
            async_db_session,
            InstitutionPayload(
                account_id=account_id, party_id=party_id, routing_number="021000021"
            ),
            None,
        )
        await async_db_session.commit()

        assert (await institution_of(async_db_session, party_id)).routing_number == (
            "021000021"
        )

    def test_a_routing_number_that_cannot_be_one_is_refused(self) -> None:
        from pydantic import ValidationError

        from app.services.finance.domains.writes.accounts import InstitutionPayload

        with pytest.raises(ValidationError):
            InstitutionPayload(account_id=1, party_id=1, routing_number="020100021")

    @pytest.mark.asyncio
    async def test_a_contact_nobody_filed(self, async_db_session: AsyncSession) -> None:
        from app.services.finance.domains.writes.accounts import (
            InstitutionPayload,
            institution_execute,
        )

        _party_id, account_id = await self._chase(async_db_session)
        with pytest.raises(ValueError, match="contact"):
            await institution_execute(
                async_db_session,
                InstitutionPayload(account_id=account_id, party_id=999_999),
                None,
            )

    def test_it_is_in_the_one_queue(self) -> None:
        from app.services.finance.domains.writes.registry import executor_for

        assert executor_for("account.institution").title
