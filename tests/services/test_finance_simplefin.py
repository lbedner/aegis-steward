"""SimpleFIN as a bank connection (#370).

SimpleFIN Bridge is what open-source budgeting apps use for US banks: the
user connects banks on its site and hands the app a one-time setup token,
which the app trades for a private, read-only access URL. ``DEMO`` is a
trimmed copy of a real response from the Bridge's demo token (protocol
version 2, 2026-10-03), so the mapping is tested against the shape the
server actually sends.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.simplefin import (
    SimpleFINClient,
    SimpleFINError,
)
from app.services.finance.constants import Provider
from app.services.finance.models import (
    FinanceAccount,
    FinanceConnection,
    FinanceTransaction,
)
from app.services.finance.utils import current_date

ACCESS_URL = "https://user:secret@bridge.example/simplefin"
SETUP_TOKEN = (
    "aHR0cHM6Ly9icmlkZ2UuZXhhbXBsZS9zaW1wbGVmaW4vY2xhaW0vVE9LRU4="  # .../claim/TOKEN
)

DEMO: dict[str, Any] = {
    "errlist": [],
    "connections": [
        {
            "conn_id": "CON-SIMPLEFIN-DEMO",
            "name": "SimpleFIN Demo",
            "org_id": "simplefin.demoorg",
            "org_name": "SimpleFIN Bridge",
        }
    ],
    "accounts": [
        {
            "id": "Demo Savings",
            "name": "SimpleFIN Savings",
            "conn_id": "CON-SIMPLEFIN-DEMO",
            "currency": "USD",
            "balance": "115105.51",
            "available-balance": "115105.51",
            "balance-date": 1791072000,
            "transactions": [
                {
                    "id": "1791014400",
                    "posted": 1791014400,
                    "amount": "-15.50",
                    "description": "Fishing bait",
                    "payee": "John's Fishin Shack",
                    "memo": "JOHNS FISHIN SHACK BAIT",
                    "transacted_at": 1791014400,
                },
                {
                    "id": "1791043200",
                    "posted": 1791043200,
                    "amount": "-125.50",
                    "description": "Grocery store",
                    "payee": "Grocery store",
                    "memo": "LOCAL GROCER STORE #1133",
                    "transacted_at": 1791043200,
                },
            ],
        },
        {
            "id": "Demo Checking",
            "name": "SimpleFIN Checking",
            "conn_id": "CON-SIMPLEFIN-DEMO",
            "currency": "USD",
            "balance": "25584.47",
            "available-balance": "25584.47",
            "balance-date": 1791072000,
            "transactions": [
                {
                    "id": "c-1",
                    "posted": 1791014400,
                    "amount": "2500.00",
                    "description": "Payroll",
                    "payee": "Acme Corp",
                },
                {
                    # Pending: posted is 0 until it posts.
                    "id": "c-2",
                    "posted": 0,
                    "transacted_at": 1791043200,
                    "amount": "-40.00",
                    "description": "Coffee",
                    "pending": True,
                },
            ],
        },
        {
            "id": "Demo Card",
            "name": "Rewards Visa",
            "conn_id": "CON-SIMPLEFIN-DEMO",
            "currency": "USD",
            "balance": "-812.30",
            "balance-date": 1791072000,
            "transactions": [],
        },
    ],
}


class FakeSimpleFINClient:
    """Answers like the Bridge, and records what it was asked."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or DEMO
        self.claimed: list[str] = []
        self.windows: list[tuple[date, date]] = []

    async def claim(self, setup_token: str) -> str:
        self.claimed.append(setup_token)
        return ACCESS_URL

    async def accounts(
        self, access_url: str, *, start: date, end: date
    ) -> dict[str, Any]:
        assert access_url == ACCESS_URL
        self.windows.append((start, end))
        return self.payload


async def _connect(db: AsyncSession, client: FakeSimpleFINClient) -> FinanceConnection:
    await connections.connect_simplefin(
        db, owner_user_id=1, setup_token=f" {SETUP_TOKEN}\n", client=client
    )
    await db.commit()
    (connection,) = (
        await db.exec(
            select(FinanceConnection).where(
                FinanceConnection.provider == Provider.SIMPLEFIN
            )
        )
    ).all()
    return connection


async def _rows(db: AsyncSession) -> list[FinanceTransaction]:
    return list(
        (
            await db.exec(
                select(FinanceTransaction).where(
                    FinanceTransaction.source == Provider.SIMPLEFIN,
                    FinanceTransaction.deleted_at.is_(None),
                )
            )
        ).all()
    )


class TestConnecting:
    @pytest.mark.asyncio
    async def test_the_token_is_claimed_and_the_banks_arrive(
        self, async_db_session: AsyncSession
    ) -> None:
        client = FakeSimpleFINClient()

        connection = await _connect(async_db_session, client)

        assert client.claimed == [SETUP_TOKEN]  # trimmed, as pasted
        assert connection.label == "SimpleFIN Demo"
        assert connection.status == "healthy"
        # The access URL is the credential: stored encrypted, never as is.
        assert connection.access_token_encrypted
        assert "secret" not in connection.access_token_encrypted
        accounts = {
            a.name: a
            for a in (
                await async_db_session.exec(
                    select(FinanceAccount).where(
                        FinanceAccount.provider == Provider.SIMPLEFIN
                    )
                )
            ).all()
        }
        assert set(accounts) == {
            "SimpleFIN Savings",
            "SimpleFIN Checking",
            "Rewards Visa",
        }
        assert accounts["SimpleFIN Savings"].current_balance == 11510551
        assert (
            accounts["SimpleFIN Savings"].account_type,
            accounts["SimpleFIN Savings"].classification,
        ) == ("savings", "asset")
        assert accounts["SimpleFIN Checking"].account_type == "checking"
        # No type comes from SimpleFIN: a card's negative balance says it.
        assert (
            accounts["Rewards Visa"].account_type,
            accounts["Rewards Visa"].classification,
        ) == ("credit_card", "liability")

    @pytest.mark.asyncio
    async def test_transactions_land_in_this_apps_terms(
        self, async_db_session: AsyncSession
    ) -> None:
        await _connect(async_db_session, FakeSimpleFINClient())

        rows = {r.external_id: r for r in await _rows(async_db_session)}

        assert set(rows) == {"1791014400", "1791043200", "c-1", "c-2"}
        bait = rows["1791014400"]
        assert (bait.amount, bait.name, bait.original_description) == (
            -1550,
            "John's Fishin Shack",
            "JOHNS FISHIN SHACK BAIT",
        )
        assert rows["c-1"].amount == 250000  # a deposit stays positive
        coffee = rows["c-2"]
        assert coffee.pending is True
        assert coffee.date_ is not None  # dated by when it was made


class TestTheDemo:
    @pytest.mark.asyncio
    async def test_a_demo_token_is_a_sandbox_connection(
        self, async_db_session: AsyncSession
    ) -> None:
        """The Bridge's demo tokens (its developer page, fixed fake data)
        sign in as ``demo``: the connection says sandbox, like Plaid's."""
        client = FakeSimpleFINClient()
        demo = "https://demo:demo@beta-bridge.simplefin.org/simplefin"

        async def claim(_token: str) -> str:
            return demo

        async def accounts(_url: str, **_: Any) -> dict[str, Any]:
            return DEMO

        client.claim = claim  # type: ignore[method-assign]
        client.accounts = accounts  # type: ignore[method-assign]

        await connections.connect_simplefin(
            async_db_session, owner_user_id=1, setup_token=SETUP_TOKEN, client=client
        )

        (connection,) = (
            await async_db_session.exec(
                select(FinanceConnection).where(
                    FinanceConnection.provider == Provider.SIMPLEFIN
                )
            )
        ).all()
        assert connection.environment == "sandbox"

    def test_a_real_bridge_is_production(self) -> None:
        """Real tokens come from the beta host too now (the create page
        redirects there): only the demo's own sign-in is the demo (#400)."""
        from app.services.finance.adapters.providers.simplefin import is_demo

        assert not is_demo(ACCESS_URL)
        assert not is_demo("https://u:p@bridge.simplefin.org/simplefin")
        assert not is_demo("https://u:p@beta-bridge.simplefin.org/simplefin")
        assert is_demo("https://demo:demo@beta-bridge.simplefin.org/simplefin")


class TestSyncing:
    @pytest.mark.asyncio
    async def test_a_second_sync_adds_nothing_twice(
        self, async_db_session: AsyncSession
    ) -> None:
        client = FakeSimpleFINClient()
        connection = await _connect(async_db_session, client)

        result = await connections.sync_one_connection(
            async_db_session,
            connection.id,
            owner_user_id=1,
            clients={Provider.SIMPLEFIN: client},
        )

        assert result is not None
        assert (result.added, result.updated) == (0, 4)  # each window, once
        assert len(await _rows(async_db_session)) == 4

    @pytest.mark.asyncio
    async def test_each_pull_stays_within_45_days_and_overlaps_the_last(
        self, async_db_session: AsyncSession
    ) -> None:
        """The Bridge warns past 45 days a request and asks for a few days
        of overlap, so nothing posted late is missed. The first sync fills
        90 days of history, two requests' worth."""
        client = FakeSimpleFINClient()
        connection = await _connect(async_db_session, client)
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        today = current_date()
        assert client.windows == [
            (today - timedelta(days=89), today - timedelta(days=45)),
            (today - timedelta(days=44), today),
            (today - timedelta(days=5), today),  # the next sync
        ]

    @pytest.mark.asyncio
    async def test_the_bridges_errors_are_shown(
        self, async_db_session: AsyncSession
    ) -> None:
        """SimpleFIN asks apps to always show its ``errlist``: a bank that
        needs the user says so on the connection, and the rest still syncs."""
        payload = {
            **DEMO,
            "errlist": [
                {"code": "con.auth", "msg": "Chase needs you to log in again."}
            ],
        }

        connection = await _connect(async_db_session, FakeSimpleFINClient(payload))

        assert connection.status == "error"
        assert connection.status_detail == "Chase needs you to log in again."
        assert len(await _rows(async_db_session)) == 4

    @pytest.mark.asyncio
    async def test_a_note_for_the_developer_is_not_the_users_problem(
        self, async_db_session: AsyncSession
    ) -> None:
        """``gen.api`` is "meant for the developer and not the user" (the
        protocol): it is logged, and the connection stays healthy."""
        payload = {
            **DEMO,
            "errlist": [
                {"code": "gen.api", "msg": "Requested date range exceeds 45 days."}
            ],
        }

        connection = await _connect(async_db_session, FakeSimpleFINClient(payload))

        assert (connection.status, connection.status_detail) == ("healthy", None)

    @pytest.mark.asyncio
    async def test_the_registry_syncs_and_disconnects_it(
        self, async_db_session: AsyncSession
    ) -> None:
        client = FakeSimpleFINClient()
        connection = await _connect(async_db_session, client)

        results = await connections.sync_owner_connections(
            async_db_session, owner_user_id=1, clients={Provider.SIMPLEFIN: client}
        )
        removed, revoke = await connections.disconnect_connection(
            async_db_session, connection.id, owner_user_id=1
        )

        assert [r.connection_id for r in results] == [connection.id]
        # Access is revoked at SimpleFIN, by the user: nothing to call here.
        assert removed is True and revoke is None


class TestTheClient:
    @pytest.mark.asyncio
    async def test_claiming_posts_once_to_the_decoded_url(self) -> None:
        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=ACCESS_URL)

        client = SimpleFINClient(transport=httpx.MockTransport(answer))

        assert await client.claim(SETUP_TOKEN) == ACCESS_URL
        (request,) = seen
        assert request.method == "POST"
        assert str(request.url) == "https://bridge.example/simplefin/claim/TOKEN"

    @pytest.mark.asyncio
    async def test_a_used_token_says_so(self) -> None:
        client = SimpleFINClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(403))
        )

        # The protocol asks to warn that the token may be compromised.
        with pytest.raises(SimpleFINError, match="compromised"):
            await client.claim(SETUP_TOKEN)

    @pytest.mark.asyncio
    async def test_a_token_that_is_not_one_says_so(self) -> None:
        with pytest.raises(SimpleFINError, match="setup token"):
            await SimpleFINClient().claim("not a token")

    @pytest.mark.asyncio
    async def test_accounts_asks_for_version_two_with_basic_auth(self) -> None:
        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=DEMO)

        client = SimpleFINClient(transport=httpx.MockTransport(answer))

        payload = await client.accounts(
            ACCESS_URL, start=date(2026, 7, 6), end=date(2026, 10, 3)
        )

        assert payload == DEMO
        (request,) = seen
        assert request.url.host == "bridge.example"
        assert request.url.path == "/simplefin/accounts"
        assert request.url.params["version"] == "2"
        assert request.url.params["pending"] == "1"
        assert int(request.url.params["start-date"]) < int(
            request.url.params["end-date"]
        )
        assert request.headers["authorization"].startswith("Basic ")
        assert "secret" not in str(request.url)  # the credential rides the header


class TestWhatTheReviewFound:
    @pytest.mark.asyncio
    async def test_an_account_only_in_the_older_window_keeps_its_rows(
        self, async_db_session: AsyncSession
    ) -> None:
        """A first sync reads two windows: an account closed 45-90 days ago
        is only in the older one, and its transactions must still land."""
        older = {
            **DEMO,
            "accounts": [
                {
                    "id": "Closed Card",
                    "name": "Old Visa",
                    "currency": "USD",
                    "balance": "0.00",
                    "transactions": [
                        {"id": "o-1", "posted": 1785000000, "amount": "-9.99"}
                    ],
                }
            ],
        }

        class TwoWindows(FakeSimpleFINClient):
            async def accounts(self, access_url: str, **window: Any) -> dict[str, Any]:
                self.windows.append((window["start"], window["end"]))
                return older if len(self.windows) == 1 else DEMO

        await _connect(async_db_session, TwoWindows())

        assert "o-1" in {r.external_id for r in await _rows(async_db_session)}

    def test_a_savings_account_at_a_credit_union_is_savings(self) -> None:
        from app.services.finance.adapters.providers.connections.simplefin_sync.mapping import (
            simplefin_accounts,
        )

        (account,) = simplefin_accounts(
            {"accounts": [{"id": "a", "name": "Credit Union Savings", "balance": "10"}]}
        )
        assert (account.account_type, account.classification) == ("savings", "asset")

    def test_a_half_cent_rounds_as_every_other_amount_does(self) -> None:
        """Half up, as ``to_cents`` rounds imports (#215), not half-even."""
        from app.services.finance.adapters.providers.connections.simplefin_sync.mapping import (
            simplefin_transactions,
        )

        (txn,) = simplefin_transactions(
            {
                "accounts": [
                    {
                        "id": "a",
                        "transactions": [{"id": "t", "posted": 1, "amount": "0.125"}],
                    }
                ]
            }
        )
        assert txn.amount == 13

    def test_the_activity_page_names_a_simplefin_run(self) -> None:
        from types import SimpleNamespace

        from app.services.finance.adapters.importers.imports import run_title

        assert run_title(SimpleNamespace(source_type="simplefin_sync")) == "SimpleFIN"

    @pytest.mark.asyncio
    async def test_another_owners_bank_cannot_be_purged(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.service import FinanceService

        connection = await _connect(async_db_session, FakeSimpleFINClient())

        removed = await FinanceService(async_db_session).purge_connection_accounts(
            connection.id, owner_user_id=2
        )

        assert removed == 0
        assert len(await _rows(async_db_session)) == 4


@pytest.mark.asyncio
async def test_the_scheduled_sync_reaches_a_simplefin_household(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 6-hourly job synced only owners with Plaid or SnapTrade, so a
    household on SimpleFIN alone never synced on its own."""
    from app.core.db import get_async_session
    from app.services.finance import jobs

    synced: list[int | None] = []

    async def sync(_db: object, *, owner_user_id: int | None = None) -> list[Any]:
        synced.append(owner_user_id)
        return []

    monkeypatch.setattr(connections, "sync_owner_connections", sync)
    async with get_async_session() as db:
        row = FinanceConnection(
            owner_user_id=4242,
            provider=Provider.SIMPLEFIN,
            connection_type="aggregator_token",
            environment="production",
            status="healthy",
        )
        db.add(row)
        await db.commit()
        row_id = row.id
    try:
        await jobs.finance_sync_connections_job()
    finally:
        async with get_async_session() as db:
            stored = await db.get(FinanceConnection, row_id)
            if stored is not None:
                await db.delete(stored)
                await db.commit()

    assert 4242 in synced
