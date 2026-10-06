"""A bank that needs you shows on every page (#310).

A link in error, needing a sign-in, or gone stale used to be visible only
in Settings, in words like "Login_Required". Now every page carries a
banner while one does, it offers Reconnect where the bank can be
reconnected in place (Plaid's update mode), and statuses read as words.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.services.finance.constants import CONNECTION_STATUS
from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, text

ATTENTION = "/settings/connections/attention"


async def _set(db: AsyncSession, connection_id: int, **fields: object) -> None:
    from app.services.finance.models import FinanceConnection

    row = await db.get(FinanceConnection, connection_id)
    assert row is not None
    for name, value in fields.items():
        setattr(row, name, value)
    db.add(row)
    await db.commit()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    jobs: list[tuple] = []

    class Pool:
        async def enqueue_job(self, *args: object, **_: object) -> None:
            jobs.append(args)

    async def pool(name: str) -> tuple[Pool, str]:
        return Pool(), name

    monkeypatch.setattr("app.components.worker.pools.get_queue_pool", pool)
    return jobs


class TestTheBanner:
    def test_every_page_asks_for_it_and_a_fragment_does_not(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        one(client.get("/accounts").text, f'[hx-get="{ATTENTION}"]')
        none(hx.get("/accounts").text, f'[hx-get="{ATTENTION}"]')

    async def test_a_bank_that_is_fine_says_nothing(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(async_db_session, connection, last_successful_sync_at=utcnow())

        none(hx.get(ATTENTION).text, "[data-attention] li")

    async def test_a_bank_needing_a_sign_in_says_so_in_words_with_reconnect(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(
            async_db_session,
            connection,
            status="login_required",
            needs_user_action=True,
            last_successful_sync_at=utcnow(),
        )

        row = one(hx.get(ATTENTION).text, "[data-attention] li")
        assert "Chase" in text(row)
        assert CONNECTION_STATUS["login_required"][0] in text(row)
        assert "Login_Required" not in text(row)
        one(row, f'[hx-post="/settings/connections/{connection}/reconnect"]')

    async def test_a_bank_that_stopped_syncing_is_stale(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(
            async_db_session,
            connection,
            last_successful_sync_at=utcnow() - timedelta(days=10),
        )

        row = one(hx.get(ATTENTION).text, "[data-attention] li")
        assert "10 days" in text(row)


class TestTheCard:
    async def test_the_card_says_the_status_in_words_and_offers_reconnect(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(
            async_db_session,
            connection,
            status="login_required",
            needs_user_action=True,
        )

        card = one(client.get("/settings").text, f"#connection-{connection}")
        assert CONNECTION_STATUS["login_required"][0] in text(card)
        one(card, f'[hx-post="/settings/connections/{connection}/reconnect"]')


class TestReconnecting:
    async def test_reconnect_opens_the_banks_own_page_and_waits_for_you(
        self,
        hx: TestClient,
        async_db_session: AsyncSession,
        connection: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.finance.adapters.providers import connections

        async def _relink(_db: object, connection_id: int, **_: object):
            return "https://hosted.example/update", "link-token"

        monkeypatch.setattr(connections, "relink_connection", _relink)
        await _set(async_db_session, connection, status="login_required")

        dialog = hx.post(f"/settings/connections/{connection}/reconnect").text

        opener = one(dialog, 'a[target="_blank"]')
        assert opener.get("href") == "https://hosted.example/update"
        one(
            dialog, f'form[hx-post="/settings/connections/{connection}/reconnect/done"]'
        )

    def test_done_checks_the_bank_again_on_the_worker(
        self, client: TestClient, connection: int, queued: list[tuple]
    ) -> None:
        response = client.post(f"/settings/connections/{connection}/reconnect/done")

        assert "dialog:close" in response.headers["HX-Trigger-After-Settle"]
        assert queued == [("finance_sync_connection_task", connection, None)]

    def test_reconnect_where_the_bank_cannot_says_so(
        self, hx: TestClient, connection: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.finance.adapters.providers import connections

        async def _relink(*_a: object, **_k: object) -> None:
            return None

        monkeypatch.setattr(connections, "relink_connection", _relink)

        dialog = hx.post(f"/settings/connections/{connection}/reconnect")

        assert dialog.status_code == 422
        one(dialog.text, "[role=alert]")
        assert select(dialog.text, 'a[target="_blank"]') == []
