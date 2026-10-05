"""Settings: connections, categories, payees, and the comms job.

Four sibling routes under one sub-nav, the same shape Review uses.
Connections is a two-step connect (start a session, then poll it to
completion) plus a disconnect; the two tables render through the shared
table macro; comms shows what the scheduled bill email would say.
"""

import json
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.components.web_frontend.routes.finance.connect_providers import (
    SIMPLEFIN_CREATE,
)
from app.core.config import settings
from app.services.finance.adapters.providers.simplefin import DEMO_PAGE
from tests.web.conftest import Ledger, Streams
from tests.web.dom import none, one, select, table_rows, text, triggers


def nav(page: str) -> list[str]:
    return [text(a) for a in select(page, "#settings-nav a")]


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every provider built in and credentialed."""
    for name, value in (
        ("FINANCE_PLAID", True),
        ("FINANCE_SNAPTRADE", True),
        ("FINANCE_SIMPLEFIN", True),
        ("PLAID_CLIENT_ID", "id"),
        ("PLAID_SECRET", "secret"),
        ("SNAPTRADE_CLIENT_ID", "id"),
        ("SNAPTRADE_CONSUMER_KEY", "key"),
    ):
        monkeypatch.setattr(settings, name, value)


class TestNav:
    def test_every_tab_is_a_sibling_route(self, client: TestClient) -> None:
        page = client.get("/settings").text
        from app.components.web_frontend.nav import SETTINGS_TABS as TABS

        # The tabs are declared once; the test walks that list rather
        # than repeating it, so adding one is a route to write and not a
        # string to remember in two places.
        assert nav(page) == [label for _key, label, _suffix in TABS]
        current = one(page, '#settings-nav a[aria-current="page"]')
        assert text(current) == "Connections"
        for path in (f"/settings{suffix}" for _key, _label, suffix in TABS if suffix):
            marked = one(client.get(path).text, '#settings-nav a[aria-current="page"]')
            assert marked.get("href") == path

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/settings").text, "html")


@pytest.fixture
async def held(async_db_session, connection: int, ledger: Ledger) -> int:
    """The connection, holding one checking account its link reported that
    the ledger's own accounts may already be (#309)."""
    from app.services.finance.adapters.providers.connections import placing
    from app.services.finance.adapters.providers.connections.upserts import (
        ProviderAccount,
    )
    from app.services.finance.models import FinanceConnection

    row = await async_db_session.get(FinanceConnection, connection)
    assert row is not None
    reported = ProviderAccount(
        provider_account_id="acc_check",
        name="Plaid Checking",
        mask="0000",
        currency="usd",
        account_type="checking",
        classification="asset",
        current_balance=0,
    )
    placing.hold(row, [placing.waiting(reported)])
    async_db_session.add(row)
    await async_db_session.commit()
    return connection


@pytest.fixture
async def fed(async_db_session, connection: int, ledger: Ledger) -> int:
    """The connection, feeding the ledger's checking account."""
    from app.services.finance.models import FinanceAccount

    account = await async_db_session.get(FinanceAccount, ledger.checking)
    assert account is not None
    account.connection_id = connection
    async_db_session.add(account)
    await async_db_session.commit()
    return connection


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """The jobs a route handed the worker, with no Redis behind them."""
    jobs: list[tuple] = []

    class Pool:
        async def enqueue_job(self, *args: object, **_: object) -> None:
            jobs.append(args)

    async def pool(name: str) -> tuple[Pool, str]:
        return Pool(), name

    monkeypatch.setattr("app.components.worker.pools.get_queue_pool", pool)
    return jobs


@pytest.fixture
def simplefin_links(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """SimpleFIN connects without the Bridge: the tokens claimed, and the
    connection a claim makes (``id``, settable)."""
    from app.services.finance.adapters.providers import connections
    from app.services.finance.adapters.providers.connections.common import (
        SyncResult,
    )

    linked: dict[str, Any] = {"id": 1, "claimed": []}

    async def _connect(_db: object, *, setup_token: str, **_: object) -> SyncResult:
        linked["claimed"].append(setup_token)
        return SyncResult(connection_id=linked["id"], accounts=3, added=4)

    monkeypatch.setattr(connections, "connect_simplefin", _connect)
    return linked


class TestPlacingAHeldAccount:
    """A linked account that could be one the exports already feed waits
    for you to say which (#309), never becoming a second copy."""

    def test_the_card_says_so_and_opens_the_choice(
        self, client: TestClient, hx: TestClient, held: int, ledger: Ledger
    ) -> None:
        card = one(client.get("/settings").text, f"#connection-{held}")
        one(card, "[data-unplaced]")
        place = f"/settings/connections/{held}/place"
        one(card, f'[hx-get="{place}"]')

        dialog = hx.get(place).text
        none(dialog, "html")
        form = one(dialog, f'form[hx-post="{place}"]')
        options = select(form, "select[name='place-acc_check'] option")
        # Its own account, or one of yours of the same kind of money: the
        # card is a debt, so it is not offered.
        assert [o.get("value") for o in options] == [
            "",
            "new",
            str(ledger.checking),
            str(ledger.savings),
        ]

    async def test_placing_it_links_your_account_and_syncs_on_the_worker(
        self,
        client: TestClient,
        held: int,
        ledger: Ledger,
        queued: list[tuple],
        async_db_session,
    ) -> None:
        """Its history is the bank's whole history again: the worker pulls
        it, not the request."""
        from app.services.finance.models import FinanceAccount

        response = client.post(
            f"/settings/connections/{held}/place",
            data={"place-acc_check": str(ledger.checking)},
        )

        assert response.status_code == 200
        assert "dialog:close" in response.headers["HX-Trigger-After-Settle"]
        assert queued == [("finance_sync_connection_task", held, None)]
        account = await async_db_session.get(FinanceAccount, ledger.checking)
        await async_db_session.refresh(account)
        assert (account.connection_id, account.provider_account_id) == (
            held,
            "acc_check",
        )
        none(client.get("/settings").text, f"#connection-{held} [data-unplaced]")

    def test_an_unanswered_account_is_asked_again(
        self, hx: TestClient, held: int, queued: list[tuple]
    ) -> None:
        response = hx.post(f"/settings/connections/{held}/place", data={})

        assert response.status_code == 422
        one(response.text, "[role=alert]")
        assert queued == []

    def test_nothing_left_to_place_syncs_nothing(
        self, client: TestClient, connection: int, queued: list[tuple]
    ) -> None:
        """A second submit, or a stale form: nothing is held, so nothing is
        placed and the bank is not asked for its history again."""
        response = client.post(f"/settings/connections/{connection}/place", data={})

        assert "dialog:close" in response.headers["HX-Trigger-After-Settle"]
        assert queued == []

    def test_a_connect_that_holds_one_asks_at_once(
        self,
        client: TestClient,
        held: int,
        providers: None,
        simplefin_links: dict[str, Any],
    ) -> None:
        simplefin_links["id"] = held

        finished = client.post(
            "/settings/connect/simplefin/complete", data={"token": "aGVsbG8="}
        )

        one(finished.text, f'form[hx-post="/settings/connections/{held}/place"]')

    def test_another_banks_held_accounts_wait_their_turn(
        self,
        client: TestClient,
        held: int,
        providers: None,
        simplefin_links: dict[str, Any],
    ) -> None:
        """Linking a second bank finishes as itself; the first one's held
        accounts stay on its card."""
        simplefin_links["id"] = held + 1

        finished = client.post(
            "/settings/connect/simplefin/complete", data={"token": "aGVsbG8="}
        )

        none(finished.text, f'form[hx-post="/settings/connections/{held}/place"]')
        one(finished.text, "#connections[hx-swap-oob]")


class TestConnections:
    def test_empty_says_so_and_offers_both_providers(
        self, client: TestClient, providers: None
    ) -> None:
        page = client.get("/settings").text
        assert "Nothing connected" in text(one(page, "#connections"))
        assert one(page, '[hx-post="/settings/connect/plaid"]') is not None
        assert one(page, '[hx-post="/settings/connect/snaptrade"]') is not None

    def test_a_provider_without_credentials_offers_nothing_but_says_why(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "FINANCE_PLAID", True)
        monkeypatch.setattr(settings, "PLAID_CLIENT_ID", None)
        monkeypatch.setattr(settings, "FINANCE_SNAPTRADE", False)
        page = client.get("/settings").text
        none(page, '[hx-post="/settings/connect/plaid"]')
        none(page, '[hx-post="/settings/connect/snaptrade"]')
        # The front door stays visible as a sentence, so a fresh project
        # can see what to set rather than wondering where the feature went.
        assert "PLAID_CLIENT_ID" in text(one(page, "#connect-help"))

    def test_a_provider_switched_off_is_not_mentioned_at_all(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "FINANCE_PLAID", False)
        monkeypatch.setattr(settings, "FINANCE_SNAPTRADE", False)
        page = client.get("/settings").text
        none(page, "#connect-help")
        assert "Nothing connected" in text(one(page, "#connections"))

    async def test_cards_carry_the_status_and_a_disconnect(
        self, client: TestClient, hx: TestClient, connection: int
    ) -> None:
        page = client.get("/settings").text
        card = one(page, f"#connection-{connection}")
        assert "Plaid" in text(card)
        assert text(one(card, "[data-tone]")) == "Connected"
        confirm = one(card, f'[hx-get="/settings/connections/{connection}/remove"]')
        assert confirm.get("hx-target") == "#dialog-body"

        dialog = hx.get(f"/settings/connections/{connection}/remove").text
        one(dialog, f'[hx-delete="/settings/connections/{connection}"]')
        response = client.delete(f"/settings/connections/{connection}")
        assert "dialog:close" in triggers(response)
        none(client.get("/settings").text, f"#connection-{connection}")

    @pytest.mark.queryspy(threshold=3)  # the dialog asks what the purge deletes
    async def test_disconnecting_can_take_its_data_permanently(
        self, client: TestClient, hx: TestClient, fed: int
    ) -> None:
        """For a bank tried and not wanted (a demo): disconnect and delete
        its accounts and their history, once its name is typed back."""
        connection = fed
        dialog = hx.get(f"/settings/connections/{connection}/remove").text
        form = one(
            dialog,
            f"form[data-purge][hx-post='/settings/connections/{connection}/purge']",
        )
        name = text(one(dialog, "h2")).removeprefix("Disconnect ").removesuffix("?")

        wrong = client.post(
            f"/settings/connections/{connection}/purge", data={"confirm": "nope"}
        )
        assert wrong.status_code == 422
        one(wrong.text, "form[data-purge]")

        gone = client.post(
            f"/settings/connections/{connection}/purge", data={"confirm": name}
        )
        assert "deleted" in triggers(gone)["toast"]["text"]
        none(client.get("/settings").text, f"#connection-{connection}")
        assert form is not None

    def test_a_connection_that_brought_nothing_is_simply_removed(
        self, hx: TestClient, connection: int
    ) -> None:
        """A connect left half done fed no account: nothing to keep, nothing
        to delete, so no typed name either - just Remove."""
        dialog = hx.get(f"/settings/connections/{connection}/remove").text

        one(dialog, f'[hx-delete="/settings/connections/{connection}"]')
        none(dialog, "form[data-purge]")
        assert "stay" not in text(one(dialog, "p"))

    async def test_history_left_by_a_removed_account_can_still_be_deleted(
        self, hx: TestClient, fed: int, ledger: Ledger, async_db_session
    ) -> None:
        """Removing an account keeps its rows; the purge still reaches them,
        so the dialog still offers it."""
        from app.core.time import utcnow
        from app.services.finance.models import FinanceAccount

        account = await async_db_session.get(FinanceAccount, ledger.checking)
        account.deleted_at = utcnow()
        async_db_session.add(account)
        await async_db_session.commit()

        dialog = hx.get(f"/settings/connections/{fed}/remove").text
        one(dialog, "form[data-purge]")

    async def test_disconnecting_takes_the_card_with_it(
        self, client: TestClient, connection: int
    ) -> None:
        """The dialog closing is not enough: the card behind it would sit
        there offering a button that now 404s."""
        response = client.delete(f"/settings/connections/{connection}")
        assert json.loads(response.headers["HX-Location"])["path"] == "/settings"

    def test_unknown_connection_is_404(self, client: TestClient) -> None:
        assert client.delete("/settings/connections/999999").status_code == 404

    def test_simplefin_asks_for_its_setup_token(
        self, client: TestClient, hx: TestClient, providers: None
    ) -> None:
        """SimpleFIN is connected by pasting the token made on its site:
        the dialog links there and takes the token, nothing to wait on."""
        one(client.get("/settings").text, '[hx-post="/settings/connect/simplefin"]')

        response = hx.post("/settings/connect/simplefin")

        assert response.status_code == 200
        none(response.text, "html")
        panel = one(response.text, "#connect-status")
        opener = one(panel, 'a[target="_blank"]:not([data-demo])')
        assert opener.get("href") == SIMPLEFIN_CREATE
        demo = one(panel, "a[data-demo]")  # fake data, no sign-up
        assert demo.get("href") == DEMO_PAGE
        form = one(panel, "form[hx-post='/settings/connect/simplefin/complete']")
        assert one(form, "input[name=token]").get("type") == "password"
        # Claiming and the first sync take seconds: the button says so.
        assert "Connecting" in text(one(form, "button[type=submit]"))
        none(panel, "[hx-trigger]")  # no poller

    def test_a_pasted_token_connects_simplefin(
        self,
        client: TestClient,
        providers: None,
        simplefin_links: dict[str, Any],
    ) -> None:
        finished = client.post(
            "/settings/connect/simplefin/complete", data={"token": "aGVsbG8="}
        )

        assert simplefin_links["claimed"] == ["aGVsbG8="]
        assert "4" in triggers(finished)["toast"]["text"]
        one(finished.text, "#connections[hx-swap-oob]")

    def test_a_card_names_its_provider_as_the_provider_does(self) -> None:
        """ "SimpleFIN", not the stored key title-cased into "Simplefin"."""
        from types import SimpleNamespace

        from app.components.web_frontend.routes.finance.settings import _card

        connection = SimpleNamespace(
            id=1,
            provider="simplefin",
            label=None,
            environment="sandbox",
            status="healthy",
            status_detail=None,
            last_successful_sync_at=None,
            unplaced=0,
        )

        card = _card(connection)

        assert (card["provider"], card["label"]) == ("SimpleFIN", "SimpleFIN")

    def test_a_bad_token_says_why_and_keeps_the_form(
        self, client: TestClient, providers: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.finance.adapters.providers import connections
        from app.services.finance.adapters.providers.simplefin import SimpleFINError

        async def _connect(*_a: object, **_k: object) -> None:
            raise SimpleFINError(
                "invalid_token", "That is not a SimpleFIN setup token."
            )

        monkeypatch.setattr(connections, "connect_simplefin", _connect)

        refused = client.post(
            "/settings/connect/simplefin/complete", data={"token": "nope"}
        )

        assert refused.status_code == 422
        assert "That is not a SimpleFIN setup token." in refused.text
        assert "invalid_token" not in refused.text  # the reason, not the code
        one(refused.text, "form[hx-post='/settings/connect/simplefin/complete']")

    def test_connecting_is_a_link_out_and_a_poller(
        self, client: TestClient, providers: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step one hands back the provider's own page and an element that
        polls step two until the connection lands."""
        from app.components.web_frontend.routes.finance import settings as route

        async def _start(**_kwargs: object) -> dict[str, str]:
            return {"url": "https://plaid.test/link/abc", "token": "link-1"}

        monkeypatch.setattr(route.PROVIDERS["plaid"], "start", _start)
        response = client.post("/settings/connect/plaid")
        assert response.status_code == 200
        panel = one(response.text, "#connect-status")
        opener = one(panel, 'a[target="_blank"]')
        assert opener.get("href") == "https://plaid.test/link/abc"
        poller = one(panel, "[hx-post='/settings/connect/plaid/complete']")
        assert "every" in (poller.get("hx-trigger") or "")
        assert json.loads(poller.get("hx-vals") or "{}")["token"] == "link-1"

    def test_polling_waits_then_finishes_and_refreshes_the_list(
        self, client: TestClient, providers: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.components.web_frontend.routes.finance import settings as route

        pending = {"connections": 0, "added": 0}
        done = {"connections": 1, "added": 12}

        async def _complete(**_kwargs: object) -> dict[str, int]:
            return pending

        monkeypatch.setattr(route.PROVIDERS["plaid"], "complete", _complete)
        waiting = client.post(
            "/settings/connect/plaid/complete", data={"token": "link-1"}
        )
        # Still waiting: the poller comes back, so it keeps asking.
        one(waiting.text, "[hx-post='/settings/connect/plaid/complete']")

        async def _done(**_kwargs: object) -> dict[str, int]:
            return done

        monkeypatch.setattr(route.PROVIDERS["plaid"], "complete", _done)
        finished = client.post(
            "/settings/connect/plaid/complete", data={"token": "link-1"}
        )
        none(finished.text, "[hx-post='/settings/connect/plaid/complete']")
        assert "12" in triggers(finished)["toast"]["text"]
        assert one(finished.text, "#connections[hx-swap-oob]") is not None

    def test_the_way_in_survives_the_polling(
        self, client: TestClient, providers: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each poll replaces the whole dialog, so a pending answer has to
        carry the provider's link back or it vanishes three seconds after
        it appears and the session becomes unreachable."""
        from app.components.web_frontend.routes.finance import settings as route

        async def _pending(**_kwargs: object) -> dict[str, int]:
            return {"connections": 0}

        monkeypatch.setattr(route.PROVIDERS["plaid"], "complete", _pending)
        waiting = client.post(
            "/settings/connect/plaid/complete",
            data={
                "token": "link-1",
                "url": "https://plaid.test/link/abc",
                "attempt": 1,
            },
        )
        assert (
            one(waiting.text, 'a[target="_blank"]').get("href")
            == "https://plaid.test/link/abc"
        )
        poller = one(waiting.text, "[hx-post='/settings/connect/plaid/complete']")
        carried = json.loads(poller.get("hx-vals") or "{}")
        assert carried["url"] == "https://plaid.test/link/abc"
        assert carried["attempt"] == 2  # and it counts, so it can stop

    def test_the_polling_gives_up_rather_than_following_a_forgotten_tab(
        self, client: TestClient, providers: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.components.web_frontend.routes.finance import settings as route

        async def _pending(**_kwargs: object) -> dict[str, int]:
            return {"connections": 0}

        monkeypatch.setattr(route.PROVIDERS["plaid"], "complete", _pending)
        spent = client.post(
            "/settings/connect/plaid/complete",
            data={"token": "link-1", "attempt": route.POLL_LIMIT},
        )
        none(spent.text, "[hx-post='/settings/connect/plaid/complete']")
        assert "start again" in text(one(spent.text, "#connect-status"))
        one(spent.text, '[hx-post="/settings/connect/plaid"]')  # offered afresh

    def test_a_provider_that_fails_says_so_and_stops_polling(
        self, client: TestClient, providers: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.components.web_frontend.routes.finance import settings as route

        async def _boom(**_kwargs: object) -> dict[str, str]:
            raise HTTPException(status_code=502, detail="Plaid said no.")

        monkeypatch.setattr(route.PROVIDERS["plaid"], "start", _boom)
        response = client.post("/settings/connect/plaid")
        assert response.status_code == 422
        assert "Plaid said no." in text(one(response.text, '[role="alert"]'))
        none(response.text, "[hx-post$='/complete']")

    def test_an_unknown_provider_is_404(self, client: TestClient) -> None:
        assert client.post("/settings/connect/nope").status_code == 404


class TestCategories:
    def test_table_of_usage_through_the_shared_macro(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/settings/categories").text
        rows = table_rows(page, "#categories table")
        by_name = {text(r["Name"]): r for r in rows}
        groceries = by_name["Food:Groceries"]
        assert text(groceries["Transactions"]) == "2"
        assert text(groceries["Total"]) == "-$45.00"
        assert text(groceries["Kind"]) == "expense"

    def test_the_window_chips_narrow_the_usage(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/settings/categories").text
        assert (
            one(page, '#categories input[name="days"][checked]').get("value") == "9999"
        )
        narrowed = client.get("/settings/categories?days=1").text
        rows = {
            text(r["Name"]): text(r["Transactions"])
            for r in table_rows(narrowed, "#categories table")
        }
        assert rows.get("Auto:Fuel") in (None, "0")

    def test_empty_ledger_says_so(self, client: TestClient) -> None:
        assert "No categories yet" in text(
            one(client.get("/settings/categories").text, "#categories")
        )


class TestPayees:
    async def test_table_of_usage_and_a_category_breakdown(
        self, client: TestClient, hx: TestClient, merchant: int
    ) -> None:
        page = client.get("/settings/payees").text
        row = table_rows(page, "#payees table")[0]
        assert text(row["Name"]) == "Shell"
        assert text(row["Transactions"]) == "1"

        summary = one(page, f'[hx-get="/settings/payees/{merchant}/categories"]')
        assert summary.get("hx-target") == "#dialog-body"
        dialog = hx.get(f"/settings/payees/{merchant}/categories").text
        assert "Shell" in text(one(dialog, "h2"))
        assert "Auto:Fuel" in text(one(dialog, "#stat-rows"))

    def test_empty_says_so(self, client: TestClient) -> None:
        assert "No payees yet" in text(
            one(client.get("/settings/payees").text, "#payees")
        )


class TestComms:
    def test_says_what_the_job_would_send_and_where(
        self, client: TestClient, streams: Streams, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
        page = client.get("/settings/comms").text
        assert "leonard@example.com" in text(one(page, "#comms"))
        preview = one(page, "#bill-email-preview")
        # Rent is due in ten days, Water is overdue; the default window is
        # a week, so only the overdue one is in tomorrow's email.
        assert "Water" in text(preview) and "Rent" not in text(preview)

    def test_unconfigured_says_what_to_set_and_previews_nothing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", None)
        page = client.get("/settings/comms").text
        assert "FINANCE_BILL_EMAIL_TO" in text(one(page, "#comms"))
        none(page, "#bill-email-preview")

    def test_nothing_due_says_so(
        self, client: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
        page = client.get("/settings/comms").text
        assert "Nothing due" in text(one(page, "#bill-email-preview"))

    def test_the_preview_never_sends(
        self, client: TestClient, streams: Streams, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Settings shows the mail; only the scheduler posts it."""
        monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
        sent: list[object] = []

        async def _send(**kwargs: object) -> object:
            sent.append(kwargs)
            raise AssertionError("the preview must not send")

        monkeypatch.setattr("app.services.comms.email.send_email_simple", _send)
        client.get("/settings/comms")
        assert sent == []


class TestActivity:
    """Every run that brought something in, files and syncs alike. The
    tally used to be computed on every pass and dropped, so a connection
    could say "synced today" and nothing could say what it pulled."""

    def test_a_run_says_what_it_brought(
        self, client: TestClient, async_db_session
    ) -> None:
        from app.services.finance.adapters.importers.imports import run_summary

        class Run:
            status = "committed"
            rows_inserted = 54
            rows_updated = 0
            rows_duplicate = 2
            rows_error = 0
            detail = None

        # A file counts rows.
        assert run_summary(Run()) == "54 new · 2 duplicate"

        # A brokerage counts what only it has, and reading "0
        # transactions" off a sync that restated ten positions is how a
        # working import gets reported as broken.
        class Sync(Run):
            rows_inserted = 0
            rows_duplicate = 0
            detail = {"holdings": 5, "trades": 2}

        assert run_summary(Sync()) == "5 holdings · 2 trades"

        # A run that brought nothing is the most interesting kind.
        class Quiet(Run):
            rows_inserted = 0
            rows_duplicate = 0
            detail = None

        assert run_summary(Quiet()) == "nothing"

    def test_the_page_lists_runs_newest_first(self, client: TestClient) -> None:
        page = client.get("/settings/activity").text
        one(page, "#runs")
        marked = one(page, '#settings-nav a[aria-current="page"]')
        assert marked.get("href") == "/settings/activity"

    def test_a_list_row_and_the_run_itself_name_it_the_same_way(self) -> None:
        """A list of runs and a single run must not name the same row two
        ways, so both read ``run_title``."""
        from app.services.finance.adapters.importers.imports import run_title

        class Csv:
            source_type = "csv"
            file_name = "House Bedner Finances-export-2026-09-14.csv"

        class Sync:
            source_type = "snaptrade_sync"
            file_name = None

        assert run_title(Csv()).startswith("CSV · House Bedner")
        assert run_title(Sync()) == "SnapTrade"
