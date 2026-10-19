"""SimpleFIN as a bank connection (#370).

SimpleFIN Bridge is what open-source budgeting apps use for US banks: the
user connects banks on its site and hands the app a one-time setup token,
which the app trades for a private, read-only access URL. ``DEMO`` is a
trimmed copy of a real response from the Bridge's demo token (protocol
version 2, 2026-10-03), so the mapping is tested against the shape the
server actually sends.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime, time, timedelta
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
    FinanceCategory,
    FinanceConnection,
    FinanceImportBatch,
    FinanceTransaction,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.services._finance_factories import seed_account, seed_stream

ACCESS_URL = "https://user:secret@bridge.example/simplefin"
CLAIM_URL = "https://bridge.simplefin.org/simplefin/claim/TOKEN"
SETUP_TOKEN = base64.b64encode(CLAIM_URL.encode()).decode()

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


class TestAClaimIsNeverThrownAway:
    """Claiming a setup token works once. A first sync that fails must not
    take the claimed access with it, or the token is spent and the bank
    never connected (#402)."""

    @pytest.mark.asyncio
    async def test_the_connection_stays_when_the_first_sync_fails(
        self, async_db_session: AsyncSession
    ) -> None:
        client = FakeSimpleFINClient()

        async def accounts(_url: str, **_: Any) -> dict[str, Any]:
            raise SimpleFINError("502", "SimpleFIN could not answer (502).")

        client.accounts = accounts  # type: ignore[method-assign]

        with pytest.raises(SimpleFINError):
            await connections.connect_simplefin(
                async_db_session,
                owner_user_id=1,
                setup_token=SETUP_TOKEN,
                client=client,
            )
        await async_db_session.rollback()  # what the request does with its error

        (connection,) = (
            await async_db_session.exec(
                select(FinanceConnection).where(
                    FinanceConnection.provider == Provider.SIMPLEFIN
                )
            )
        ).all()
        assert connection.access_token_encrypted
        assert connection.status == "error"
        assert "502" in (connection.status_detail or "")


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
    @pytest.mark.queryspy(threshold=3)  # two syncs
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
    @pytest.mark.queryspy(threshold=3)  # two syncs
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
        # The next sync reaches back to the demo's pending coffee too (#540).
        pending = datetime.fromtimestamp(1791043200, UTC).date()
        assert client.windows == [
            (today - timedelta(days=89), today - timedelta(days=45)),
            (today - timedelta(days=44), today),
            (
                max(
                    min(today - timedelta(days=5), pending), today - timedelta(days=44)
                ),
                today,
            ),
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
    @pytest.mark.queryspy(threshold=3)  # two syncs
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

    @pytest.mark.asyncio
    async def test_a_sync_that_fails_without_words_still_says_why(
        self, async_db_session: AsyncSession
    ) -> None:
        """A timeout's text is empty, and that blank was all the card and
        the run said while a bank was out for ten days (#532)."""
        client = FakeSimpleFINClient()
        connection = await _connect(async_db_session, client)

        async def silent(*_a: object, **_k: object) -> dict[str, Any]:
            raise httpx.ReadTimeout("")

        client.accounts = silent  # type: ignore[method-assign]
        await connections.sync_owner_connections(
            async_db_session, owner_user_id=1, clients={Provider.SIMPLEFIN: client}
        )

        run = (
            await async_db_session.exec(
                select(FinanceImportBatch).where(FinanceImportBatch.status == "failed")
            )
        ).one()
        assert connection.status == "error"
        assert connection.status_detail == run.error == "ReadTimeout"


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
        assert str(request.url) == CLAIM_URL

    @pytest.mark.asyncio
    async def test_a_used_token_says_so(self) -> None:
        client = SimpleFINClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(403))
        )

        # The protocol asks to warn that the token may be compromised.
        with pytest.raises(SimpleFINError, match="compromised"):
            await client.claim(SETUP_TOKEN)

    @pytest.mark.asyncio
    async def test_an_error_page_reads_as_words(self) -> None:
        """SimpleFIN answers some failures with its HTML page: the message
        is a sentence with the status, never the page (#402)."""
        page = "<!doctype html><html><head><title>SimpleFIN Bridge</title>"
        client = SimpleFINClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(502, text=page))
        )

        with pytest.raises(SimpleFINError) as raised:
            await client.accounts(
                ACCESS_URL, start=date(2026, 9, 1), end=date(2026, 9, 2)
            )

        assert "502" in raised.value.message
        assert "<" not in raised.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raised", "said"),
        [
            (httpx.ReadTimeout(""), "did not answer within 120s"),
            (
                httpx.RemoteProtocolError("Server disconnected."),
                "could not be reached: Server disconnected.",
            ),
        ],
    )
    async def test_a_bridge_that_never_answers_says_so(
        self, raised: Exception, said: str
    ) -> None:
        def answer(_request: httpx.Request) -> httpx.Response:
            raise raised

        client = SimpleFINClient(transport=httpx.MockTransport(answer))

        with pytest.raises(SimpleFINError) as failed:
            await client.accounts(
                ACCESS_URL, start=date(2026, 9, 1), end=date(2026, 9, 2)
            )

        assert said in failed.value.message

    @pytest.mark.asyncio
    async def test_a_token_that_is_not_one_says_so(self) -> None:
        with pytest.raises(SimpleFINError, match="setup token"):
            await SimpleFINClient().claim("not a token")

    @pytest.mark.asyncio
    async def test_a_token_for_any_other_host_is_refused_unsent(self) -> None:
        """A pasted token is a URL steward POSTs to: only SimpleFIN's own."""
        seen: list[httpx.Request] = []
        client = SimpleFINClient(
            transport=httpx.MockTransport(
                lambda r: seen.append(r) or httpx.Response(200)
            )
        )
        elsewhere = base64.b64encode(b"https://169.254.169.254/claim").decode()

        with pytest.raises(SimpleFINError, match="setup token"):
            await client.claim(elsewhere)
        assert seen == []

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


def _at(day: date) -> int:
    """A Bridge timestamp: noon UTC that day."""
    return int(datetime.combine(day, time(12), tzinfo=UTC).timestamp())


def _checking(*transactions: dict[str, Any]) -> dict[str, Any]:
    """The Bridge reporting one checking account and these rows."""
    return {
        **DEMO,
        "accounts": [
            {
                "id": "Demo Checking",
                "name": "SimpleFIN Checking",
                "conn_id": "CON-SIMPLEFIN-DEMO",
                "currency": "USD",
                "balance": "100.00",
                "transactions": list(transactions),
            }
        ],
    }


def _pending(row_id: str, day: date, amount: str) -> dict[str, Any]:
    return {
        "id": row_id,
        "posted": 0,
        "transacted_at": _at(day),
        "amount": amount,
        "payee": "Coffee",
        "pending": True,
    }


def _posted(row_id: str, day: date, amount: str) -> dict[str, Any]:
    return {"id": row_id, "posted": _at(day), "amount": amount, "payee": "Coffee"}


class TestAPendingChargePosts:
    """The Bridge posts a pending charge under a new id and never says
    which pending row it replaces (#540): the pending row stayed live
    beside it, and the charge counted twice."""

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_the_pending_row_goes_and_what_was_set_on_it_stays(
        self, async_db_session: AsyncSession
    ) -> None:
        day = current_date() - timedelta(days=3)
        client = FakeSimpleFINClient(_checking(_pending("p-1", day, "-40.00")))
        connection = await _connect(async_db_session, client)
        (pending,) = await _rows(async_db_session)
        food = FinanceCategory(
            owner_user_id=1, name="Food", slug="food-540", classification="expense"
        )
        async_db_session.add(food)
        await async_db_session.flush()
        bill = await seed_stream(
            FinanceService(async_db_session),
            name="Coffee",
            expected_amount=-4_000,
            next_expected_date=day,
        )
        # Filed, and matched to its bill, while it was pending.
        pending.category_id, pending.category_source = food.id, "user"
        pending.recurring_stream_id = bill.id
        async_db_session.add(pending)
        await async_db_session.commit()
        pending_id = pending.id

        client.payload = _checking(_posted("c-9", day + timedelta(days=1), "-40.00"))
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        (posted,) = await _rows(async_db_session)
        assert posted.external_id == "c-9"
        assert posted.pending_transaction_id == pending_id
        assert (posted.category_id, posted.category_source) == (food.id, "user")
        assert posted.recurring_stream_id == bill.id

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_one_that_never_posts_goes_once_its_grace_is_up(
        self, async_db_session: AsyncSession
    ) -> None:
        """A hold the bank dropped: no longer reported, no longer counted -
        after a few days, since a feed can leave a row out of one pass."""
        today = current_date()
        client = FakeSimpleFINClient(
            _checking(
                _pending("p-old", today - timedelta(days=3), "-40.00"),
                _pending("p-new", today - timedelta(days=1), "-12.00"),
            )
        )
        connection = await _connect(async_db_session, client)

        client.payload = _checking(_posted("c-9", today, "-75.00") | {"payee": "Gas"})
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        assert sorted(r.external_id for r in await _rows(async_db_session)) == [
            "c-9",
            "p-new",
        ]

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_tip_more_is_still_its_twin(
        self, async_db_session: AsyncSession
    ) -> None:
        day = current_date() - timedelta(days=3)
        client = FakeSimpleFINClient(_checking(_pending("p-1", day, "-40.00")))
        connection = await _connect(async_db_session, client)
        (pending,) = await _rows(async_db_session)
        pending.memo = "Dinner with Sam"
        async_db_session.add(pending)
        await async_db_session.commit()

        client.payload = _checking(_posted("c-9", day + timedelta(days=1), "-48.00"))
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        (posted,) = await _rows(async_db_session)
        assert (posted.amount, posted.memo) == (-4_800, "Dinner with Sam")

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_an_earlier_charge_alike_is_not_its_twin(
        self, async_db_session: AsyncSession
    ) -> None:
        """Two $5.40 McDonald's runs: the one already posted is another
        charge, and keeps the payee it was given."""
        day = current_date() - timedelta(days=3)
        earlier = _posted("c-0", day, "-5.40")
        client = FakeSimpleFINClient(_checking(earlier, _pending("p-1", day, "-5.40")))
        connection = await _connect(async_db_session, client)

        client.payload = _checking(
            earlier, _posted("c-9", day + timedelta(days=1), "-5.40")
        )
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        rows = {r.external_id: r for r in await _rows(async_db_session)}
        assert set(rows) == {"c-0", "c-9"}
        assert rows["c-0"].pending_transaction_id is None
        assert rows["c-9"].pending_transaction_id is not None

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_transfer_leg_that_posts_pairs_again(
        self, async_db_session: AsyncSession
    ) -> None:
        """A card payment paired while pending: retired by hand, the card's
        side stayed paired to a deleted row and out of every total."""
        day = current_date() - timedelta(days=3)
        card = {
            "id": "Demo Card",
            "name": "Rewards Visa",
            "conn_id": "CON-SIMPLEFIN-DEMO",
            "currency": "USD",
            "balance": "-100.00",
            "transactions": [
                {
                    "id": "v-1",
                    "posted": _at(day),
                    "amount": "500.00",
                    "payee": "Payment Thank You",
                }
            ],
        }
        paying = _pending("p-1", day, "-500.00") | {"payee": "Online Payment"}
        client = FakeSimpleFINClient(
            {**_checking(paying), "accounts": [*_checking(paying)["accounts"], card]}
        )
        connection = await _connect(async_db_session, client)

        posted = _posted("c-9", day + timedelta(days=1), "-500.00") | {
            "payee": "Online Payment"
        }
        client.payload = {
            **_checking(posted),
            "accounts": [*_checking(posted)["accounts"], card],
        }
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        rows = {r.external_id: r for r in await _rows(async_db_session)}
        assert set(rows) == {"c-9", "v-1"}
        assert rows["c-9"].transfer_pair_transaction_id == rows["v-1"].id
        assert rows["v-1"].transfer_pair_transaction_id == rows["c-9"].id

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_sync_reaches_back_to_its_oldest_pending_row(
        self, async_db_session: AsyncSession
    ) -> None:
        """Pulled daily, a sync only asked for the last five days: a pending
        row older than that was never reported again, and never went."""
        day = current_date() - timedelta(days=16)
        client = FakeSimpleFINClient(_checking(_pending("p-1", day, "-40.00")))
        connection = await _connect(async_db_session, client)

        client.payload = _checking(_posted("c-1", current_date(), "-5.00"))
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        assert client.windows[-1][0] <= day
        assert [r.external_id for r in await _rows(async_db_session)] == ["c-1"]


class TestWhatTheSecondDryRunFound:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # two syncs
    async def test_a_split_stays_behind_when_the_charge_settles_for_more(
        self, async_db_session: AsyncSession
    ) -> None:
        """A -40.00 hold split -30/-10 posting as -48.00: its lines summed
        to the hold, and $8 reached no category."""
        from app.services.finance.schemas import SplitPart

        day = current_date() - timedelta(days=3)
        client = FakeSimpleFINClient(_checking(_pending("p-1", day, "-40.00")))
        connection = await _connect(async_db_session, client)
        (pending,) = await _rows(async_db_session)
        food = FinanceCategory(
            owner_user_id=1, name="Food", slug="food-split", classification="expense"
        )
        async_db_session.add(food)
        await async_db_session.flush()
        assert pending.id is not None and food.id is not None
        await FinanceService(async_db_session).split_transaction(
            pending.id,
            [
                SplitPart(amount=3_000, category_id=food.id),
                SplitPart(amount=1_000, category_id=food.id),
            ],
            owner_user_id=1,
        )
        await async_db_session.commit()

        client.payload = _checking(_posted("c-9", day + timedelta(days=1), "-48.00"))
        await connections.sync_one_connection(
            async_db_session, connection.id, clients={Provider.SIMPLEFIN: client}
        )

        (posted,) = await _rows(async_db_session)
        assert (posted.amount, posted.is_split) == (-4_800, False)

    @pytest.mark.asyncio
    async def test_the_banks_hidden_copy_is_the_twin_and_gets_nothing(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The posting row is the bank's copy of an export's row, out of
        view: it is the twin - so next week's charge is not - and what was
        set on the hold stays off the hidden copy."""
        from app.services.finance.adapters.providers.connections.posting import (
            retire_unreported,
        )

        account = await seed_account(svc)
        assert account.id is not None
        today = current_date()

        async def row(external: str, cents: int, day: date, **flags: Any) -> Any:
            made = await svc.create_transaction(
                owner_user_id=1,
                account_id=account.id,
                amount=cents,
                txn_date=day,
                name="Adams",
                source=Provider.SIMPLEFIN,
                external_id=external,
                external_id_source=Provider.SIMPLEFIN,
                **flags,
            )
            return made

        hold = await row("p-1", -12_943, today - timedelta(days=5), pending=True)
        hold.memo = "Thanksgiving"
        copy = await row("c-9", -9_403, today - timedelta(days=4))
        copy.dedup_status = "duplicate"
        later = await row("c-10", -10_000, today - timedelta(days=1))
        async_db_session.add_all([hold, copy])
        await async_db_session.flush()

        await retire_unreported(
            async_db_session,
            source=Provider.SIMPLEFIN,
            reported={(account.id, "c-9"), (account.id, "c-10")},
            today=today,
        )

        assert copy.pending_transaction_id == hold.id
        assert copy.memo is None
        assert later.pending_transaction_id is None


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
