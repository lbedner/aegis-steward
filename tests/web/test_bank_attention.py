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
    @pytest.mark.queryspy(threshold=3)  # the portfolio twice, page and fragment
    def test_every_page_asks_for_it_and_a_fragment_does_not(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        one(client.get("/accounts").text, f'[hx-get="{ATTENTION}"]')
        none(hx.get("/accounts").text, f'[hx-get="{ATTENTION}"]')

    async def test_a_bank_that_is_fine_says_nothing(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(async_db_session, connection, last_successful_sync_at=utcnow())

        none(hx.get(ATTENTION).text, "[data-attention] [data-need]")

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

        row = one(hx.get(ATTENTION).text, "[data-attention] [data-need]")
        assert "Chase" in text(row)
        assert CONNECTION_STATUS["login_required"][0] in text(row)
        assert "Login_Required" not in text(row)
        one(
            hx.get(ATTENTION).text,
            f'[data-attention] [hx-post="/settings/connections/{connection}/reconnect"]',
        )

    async def test_a_bank_that_said_why_says_it_beside_its_fix(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        """ "Needs attention" alone hid an HSA out for ten days (#532)."""
        said = "Connection to Optum Bank HSA may need attention. Auth required"
        await _set(
            async_db_session,
            connection,
            status="error",
            status_detail=said,
            last_successful_sync_at=utcnow(),
        )

        line = one(hx.get(ATTENTION).text, "[data-attention]")
        assert "Optum Bank HSA needs you to sign in again" in text(line)
        # One problem: its fix on the line, not a link away from it.
        one(line, f'[hx-post="/settings/connections/{connection}/reconnect"]')

    async def test_a_link_to_many_banks_names_only_the_one_its_reason_does_not(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        """#547: a link to six banks led the banner with all six before
        the one that needed you - two lines of a phone's first screen.
        Its reason names that bank already."""
        await _set(
            async_db_session,
            connection,
            label="First Bank, Hometown Bank",
            status="error",
            status_detail="Connection to Hometown Bank may need attention",
            last_successful_sync_at=utcnow(),
        )

        row = one(hx.get(ATTENTION).text, "[data-attention] [data-need]")
        assert "First Bank" not in text(row)
        assert text(row).startswith("Hometown Bank needs attention")

    async def test_a_bank_that_stopped_syncing_is_stale_where_syncs_run(
        self,
        hx: TestClient,
        async_db_session: AsyncSession,
        connection: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "APP_ENV", "production")
        await _set(
            async_db_session,
            connection,
            last_successful_sync_at=utcnow() - timedelta(days=10),
        )

        row = one(hx.get(ATTENTION).text, "[data-attention] [data-need]")
        assert "10 days" in text(row)

    async def test_a_dev_stack_leaves_syncing_to_the_server(
        self,
        hx: TestClient,
        async_db_session: AsyncSession,
        connection: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A dev stack never schedules the bank sync (``server_only``,
        #532), so every link there reads a week old - by design, not a
        problem you can fix (#584)."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "APP_ENV", "dev")
        await _set(
            async_db_session,
            connection,
            last_successful_sync_at=utcnow() - timedelta(days=10),
        )

        none(hx.get(ATTENTION).text, "[data-attention] [data-need]")

    async def test_it_is_one_line_however_many_need_you(
        self,
        hx: TestClient,
        async_db_session: AsyncSession,
        connection: int,
    ) -> None:
        """A row a link pushed every page down (#584): several are one
        line that opens the list, each with its fix."""
        from app.services.finance.models import FinanceConnection

        other = FinanceConnection(
            owner_user_id=None,
            provider="simplefin",
            connection_type="aggregator_token",
            label="Hometown Bank",
            environment="production",
            status="error",
            status_detail="Connection to Hometown Bank may need attention. Auth required",
        )
        async_db_session.add(other)
        await _set(async_db_session, connection, status="login_required")

        line = one(hx.get(ATTENTION).text, "[data-attention]")
        assert len(select(line, "[data-need]")) == 1
        assert text(one(line, "[data-need]")).startswith("2 banks need you")
        review = one(line, "[data-review]")
        listed = hx.get(review.get("hx-get")).text
        assert len(select(listed, "[data-need]")) == 2
        one(listed, f'[hx-post="/settings/connections/{connection}/reconnect"]')
        one(listed, f'[hx-post="/settings/connections/{other.id}/reconnect"]')

    async def test_it_is_not_said_where_the_cards_say_it(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(async_db_session, connection, status="login_required")

        on_cards = hx.get(
            ATTENTION, headers={"HX-Current-URL": "http://testserver/settings"}
        ).text
        none(on_cards, "[data-attention] [data-need]")

    @pytest.mark.queryspy(threshold=3)  # sets the link twice
    async def test_dismissing_lasts_until_the_problems_change(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(async_db_session, connection, status="login_required")
        first = one(hx.get(ATTENTION).text, "[data-attention]")
        one(first, "[data-dismiss]")

        await _set(async_db_session, connection, status="error", status_detail="New")
        second = one(hx.get(ATTENTION).text, "[data-attention]")

        assert first.get("data-key") and first.get("data-key") != second.get("data-key")


class TestTheBridgesWords:
    """The Bridge's report, in plain words where its shape is known (#580)."""

    @pytest.mark.parametrize(
        ("said", "read"),
        [
            (
                "Connection to Optum Bank HSA may need attention. Auth required",
                "Optum Bank HSA needs you to sign in again",
            ),
            (
                "Connection to Hometown Bank may need attention",
                "Hometown Bank needs attention",
            ),
            (
                "Connection to M1  may need attention. Auth required",
                "M1 needs you to sign in again",
            ),
            (
                "Connection to A may need attention. Auth required; "
                "Connection to B may need attention. Account closed",
                "A needs you to sign in again; B needs attention: Account closed",
            ),
            (
                "Requested date range exceeds 45 days.",
                "Requested date range exceeds 45 days.",
            ),
        ],
    )
    def test_it_reads(self, said: str, read: str) -> None:
        from app.components.web_frontend.routes.finance.bank_attention import (
            readable,
        )

        assert readable(said) == read


class TestNewAccountsToAdd:
    """Plaid said the bank has accounts nobody added (#313)."""

    async def test_the_banner_offers_reconnect_to_add_them(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        await _set(
            async_db_session,
            connection,
            last_successful_sync_at=utcnow(),
            metadata_={"new_accounts": True},
        )

        row = one(hx.get(ATTENTION).text, "[data-attention] [data-need]")
        assert "New accounts to add" in text(row)
        one(
            hx.get(ATTENTION).text,
            f'[data-attention] [hx-post="/settings/connections/{connection}/reconnect"]',
        )

    async def test_done_clears_it_and_checks_the_bank(
        self,
        client: TestClient,
        hx: TestClient,
        async_db_session: AsyncSession,
        connection: int,
        queued: list[tuple],
    ) -> None:
        await _set(
            async_db_session,
            connection,
            last_successful_sync_at=utcnow(),
            metadata_={"new_accounts": True},
        )

        client.post(f"/settings/connections/{connection}/reconnect/done")

        none(hx.get(ATTENTION).text, "[data-attention] [data-need]")
        assert queued == [("finance_sync_connection_task", connection, None)]


class TestTheCard:
    async def test_the_status_is_its_dot_and_the_fix_comes_first(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        """The words ride the dot's title, not a badge beside the name;
        Sign in again is the button that fixes it, first and primary."""
        await _set(
            async_db_session,
            connection,
            status="login_required",
            needs_user_action=True,
        )

        card = one(client.get("/settings").text, f"#connection-{connection}")
        none(card, "[data-tone]")
        dot = one(card, "[data-dot]")
        assert dot.get("title") == CONNECTION_STATUS["login_required"][0]
        fix = one(card, "[data-needs] button")
        assert fix.get("hx-post") == f"/settings/connections/{connection}/reconnect"
        assert text(fix) == "Sign in again"
        assert "btn-primary" in fix.get("class", "")

    async def test_a_simplefin_bank_that_needs_a_sign_in_offers_one(
        self, client: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        """The Bridge keeps the bank's sign-in, so it is mended there; the
        card offered only Place and Disconnect, neither of which does."""
        await _set(
            async_db_session,
            connection,
            provider="simplefin",
            status="error",
            status_detail="Connection to Optum Bank HSA may need attention. Auth required",
        )

        card = one(client.get("/settings").text, f"#connection-{connection}")
        row = one(card, "[data-needs]")
        assert text(row).startswith("Optum Bank HSA needs you to sign in again")
        one(row, f'[hx-post="/settings/connections/{connection}/reconnect"]')
        # production is the usual case, not news; Disconnect is quiet text
        assert "production" not in text(card)
        assert "btn" not in one(card, "[data-disconnect]").get("class", "")


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

    async def test_a_simplefin_bank_is_signed_in_to_on_the_bridge(
        self, hx: TestClient, async_db_session: AsyncSession, connection: int
    ) -> None:
        from app.components.web_frontend.routes.finance.connect_providers import (
            SIMPLEFIN_ACCOUNT,
        )

        await _set(
            async_db_session,
            connection,
            provider="simplefin",
            status="error",
            status_detail="Connection to Optum Bank HSA may need attention",
        )

        dialog = hx.post(f"/settings/connections/{connection}/reconnect").text

        assert "Optum Bank HSA" in text(one(dialog, "[data-reason]"))
        opener = one(dialog, 'a[target="_blank"]')
        assert opener.get("href") == SIMPLEFIN_ACCOUNT
        assert text(opener) == "Open SimpleFIN Bridge"
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
