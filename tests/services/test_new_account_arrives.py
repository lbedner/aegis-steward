"""A new account at a linked bank appears and says so (#313).

A savings account opened inside an already-linked bank never showed up
until somebody re-ran the bank's own sign-in, and nothing said it had
arrived when it did. Plaid's NEW_ACCOUNTS_AVAILABLE was ignored, and a
sync that made an account made it silently.

Now Plaid's notice puts the bank in the banner with Reconnect, which
opens Plaid's own page with account selection on; and an account a
later sync makes is named in Attention - once, and never one you placed
as its own.
"""

from __future__ import annotations

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.connections import arrivals, placing
from app.services.finance.constants import NEW_ACCOUNT_INSIGHT_TYPE
from app.services.finance.models import FinanceAccount, FinanceInsight
from tests.services.test_finance_plaid import _ACCOUNTS, FakePlaidClient, _connect

NEW_ACCOUNTS = {
    "webhook_type": "ITEM",
    "webhook_code": "NEW_ACCOUNTS_AVAILABLE",
    "item_id": "item-1",
}


async def _sync(db: AsyncSession, connection, accounts: list[dict]) -> None:
    await connections.sync_plaid_connection(
        db, connection, client=FakePlaidClient(accounts, [], always=True)
    )


async def _announced(db: AsyncSession) -> list[FinanceInsight]:
    return list(
        (
            await db.exec(
                select(FinanceInsight).where(
                    FinanceInsight.insight_type == NEW_ACCOUNT_INSIGHT_TYPE
                )
            )
        ).all()
    )


class TestPlaidSaysThereAreNewAccounts:
    @pytest.mark.asyncio
    async def test_the_notice_is_kept_on_the_connection(
        self, async_db_session: AsyncSession
    ) -> None:
        connection = await _connect(async_db_session)

        result = await connections.process_plaid_webhook(
            async_db_session, NEW_ACCOUNTS, client=FakePlaidClient([], [])
        )

        assert result == "processed"
        assert arrivals.waiting_to_add(connection)
        # Not a broken link: the bank still syncs, so the status is fine.
        assert connection.status == "healthy"

    @pytest.mark.asyncio
    async def test_reconnect_then_opens_plaids_page_to_pick_them(
        self, async_db_session: AsyncSession
    ) -> None:
        connection = await _connect(async_db_session)
        client = FakePlaidClient([], [])
        await connections.relink_connection(
            async_db_session, connection.id, owner_user_id=1, client=client
        )
        arrivals.offer(connection, True)
        await connections.relink_connection(
            async_db_session, connection.id, owner_user_id=1, client=client
        )

        assert [c["account_selection"] for c in client.hosted_link_calls] == [
            False,
            True,
        ]


class TestALaterSyncNamesWhatItMade:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # three syncs
    async def test_an_account_the_bank_adds_is_named_in_attention_once(
        self, async_db_session: AsyncSession
    ) -> None:
        connection = await _connect(async_db_session)
        await _sync(async_db_session, connection, _ACCOUNTS[:1])
        assert await _announced(async_db_session) == []  # the first sync brings all

        await _sync(async_db_session, connection, _ACCOUNTS[:2])
        await _sync(async_db_session, connection, _ACCOUNTS[:2])

        (insight,) = await _announced(async_db_session)
        savings = (
            await async_db_session.exec(
                select(FinanceAccount).where(
                    FinanceAccount.provider_account_id == "acc_savings"
                )
            )
        ).one()
        assert insight.related_account_id == savings.id
        assert "Plaid Saving" in insight.title
        assert insight.status == "new"

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # a link, then the sync that makes it
    async def test_one_you_placed_as_its_own_is_not_news(
        self, async_db_session: AsyncSession
    ) -> None:
        connection = await _connect(async_db_session)
        await _sync(async_db_session, connection, _ACCOUNTS[:1])
        placing.remember(connection, placing.OWN_ACCOUNTS, ["acc_savings"])

        await _sync(async_db_session, connection, _ACCOUNTS[:2])

        assert await _announced(async_db_session) == []
