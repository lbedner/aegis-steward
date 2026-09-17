"""The numbers on an account's page: masked, and revealed on purpose.

An account number is not a caption. It is kept the way a sign-in's
password is, so the page shows four digits and asks before it shows the
rest, and no listing carries the number at all.
"""

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account
from tests.web.dom import none, one, text

NUMBER = "1234567894419"


async def _account_with_a_number(db: AsyncSession) -> int:
    from app.services.finance.domains.ledger.numbers import set_number

    account = await seed_account(FinanceService(db), name="TOTAL CHECKING (CHASE)")
    await set_number(db, int(account.id), NUMBER)
    await db.commit()
    return int(account.id)


class TestTheNumberOnThePage:
    @pytest.mark.asyncio
    async def test_it_shows_four_digits_and_offers_the_rest(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        account_id = await _account_with_a_number(async_db_session)

        page = client.get(f"/accounts/{account_id}/overview").text

        assert "4419" in text(one(page, "[data-number]"))
        assert NUMBER not in page
        one(page, f'[hx-post="/accounts/{account_id}/number/reveal"]')

    @pytest.mark.asyncio
    async def test_asking_shows_it_in_that_one_row(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        account_id = await _account_with_a_number(async_db_session)

        shown = client.post(f"/accounts/{account_id}/number/reveal")

        assert shown.status_code == 200
        assert NUMBER in text(one(shown.text, "[data-revealed]"))

    @pytest.mark.asyncio
    async def test_an_account_with_no_number_says_nothing_about_one(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        """An empty row on every ordinary account teaches people to skip
        the space it sits in."""
        account = await seed_account(FinanceService(async_db_session), name="Plain")
        await async_db_session.commit()

        page = client.get(f"/accounts/{account.id}/overview").text

        none(page, "[data-number]")


class TestTheBankSNumber:
    @pytest.mark.asyncio
    async def test_the_routing_number_shows_under_the_bank(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.accounts import (
            InstitutionPayload,
            institution_execute,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="JPMorgan Chase", kind="organization"
        )
        account_id = await _account_with_a_number(async_db_session)
        await institution_execute(
            async_db_session,
            InstitutionPayload(
                account_id=account_id,
                party_id=int(party.id),
                routing_number="021000021",
            ),
            None,
        )
        await async_db_session.commit()

        page = client.get(f"/accounts/{account_id}/overview").text

        assert "021000021" in text(one(page, "[data-routing]"))


class TestPuttingTheNumberIn:
    """Storage, a reveal and a masked row all shipped with no way to
    enter a number: the Numbers card only draws once a mask exists, and
    nothing could make one. It goes where the number already lives -
    Manage, Name and number."""

    @pytest.mark.asyncio
    async def test_the_form_offers_a_field_and_never_the_stored_number(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        account_id = await _account_with_a_number(async_db_session)

        form = client.get(f"/accounts/{account_id}/rename").text

        one(form, 'input[name="account_number"]')
        # Never pre-filled. A form that renders a secret to save one
        # keystroke has put it in the page, the cache and the screenshot.
        assert NUMBER not in form
        assert one(form, 'input[name="account_number"]').get("value") in (None, "")

    @pytest.mark.asyncio
    async def test_saving_one_stores_it_and_derives_the_mask(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.ledger.numbers import reveal_number
        from app.services.finance.service import FinanceService

        account = await seed_account(FinanceService(async_db_session), name="Fresh")
        await async_db_session.commit()

        saved = client.post(
            f"/accounts/{account.id}/rename",
            data={"name": "Fresh", "reference": "", "account_number": "1234567894419"},
        )
        assert saved.status_code == 200

        page = client.get(f"/accounts/{account.id}/overview").text
        assert "4419" in text(one(page, "[data-number]"))
        assert await reveal_number(async_db_session, int(account.id)) == "1234567894419"

    @pytest.mark.asyncio
    async def test_a_blank_leaves_the_stored_number_alone(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.ledger.numbers import reveal_number

        account_id = await _account_with_a_number(async_db_session)

        client.post(
            f"/accounts/{account_id}/rename",
            data={"name": "Renamed only", "reference": "", "account_number": ""},
        )

        assert await reveal_number(async_db_session, account_id) == NUMBER
