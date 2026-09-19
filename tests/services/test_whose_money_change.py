"""``account.whose``: an account put in somebody's name after the fact.

Whose money it is was asked once, when the account was created, which
every IMPORTED account skips. So a father's checking account read as the
household's while the county was asking what he holds, and nothing but
SQL could say otherwise (2026-09-18).
"""

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.service import PartyService
from tests.services._finance_factories import seed_account


async def _account_and_person(db: AsyncSession) -> dict[str, Any]:
    from app.services.finance.service import FinanceService

    svc = FinanceService(db)
    account = await seed_account(svc, name="IMPORTED CHECKING")
    james = await PartyService(db).create(name="James Whose", kind="person")
    await db.flush()
    return {"svc": svc, "account": account, "james": james}


class TestSheCanSayWhoseItIs:
    @pytest.mark.asyncio
    async def test_the_card_says_what_it_will_change(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.accounts import (
            WhosePayload,
            whose_describe,
            whose_execute,
        )

        made = await _account_and_person(async_db_session)
        payload = WhosePayload(
            account_id=made["account"].id, whose_party_id=made["james"].id
        )

        said = {
            row.label: row.value
            for row in await whose_describe(async_db_session, payload, None)
        }
        assert said["Account"] == "IMPORTED CHECKING"
        assert said["Held for"] == "James Whose"
        # What it was, because "held for James" reads as a confirmation
        # of something already true unless the card says otherwise.
        assert said["Was"] == "Ours"

        await whose_execute(async_db_session, payload, None)
        await async_db_session.commit()
        await async_db_session.refresh(made["account"])
        assert made["account"].subject_id is not None

    @pytest.mark.asyncio
    async def test_it_can_hand_the_account_back(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.accounts import (
            WhosePayload,
            whose_describe,
            whose_execute,
        )

        made = await _account_and_person(async_db_session)
        await whose_execute(
            async_db_session,
            WhosePayload(
                account_id=made["account"].id, whose_party_id=made["james"].id
            ),
            None,
        )
        await async_db_session.flush()

        back = WhosePayload(account_id=made["account"].id, whose_party_id=None)
        said = {
            row.label: row.value
            for row in await whose_describe(async_db_session, back, None)
        }
        assert said["Held for"] == "Ours"
        assert said["Was"] == "James Whose"

        await whose_execute(async_db_session, back, None)
        await async_db_session.commit()
        await async_db_session.refresh(made["account"])
        assert made["account"].subject_id is None

    @pytest.mark.asyncio
    async def test_an_account_that_is_not_there_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.accounts import (
            WhosePayload,
            whose_describe,
        )

        with pytest.raises(ValueError, match="999999"):
            await whose_describe(
                async_db_session, WhosePayload(account_id=999999), None
            )

    def test_it_is_registered_as_a_change_type(self) -> None:
        from app.services.finance.domains import writes

        assert "account.whose" in writes.registered_change_types()
