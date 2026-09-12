"""Settings: connections, categories, payees, and the comms job.

Four sibling routes under one sub-nav, the same shape Review uses.
Connections is a two-step connect (start a session, then poll it to
completion) plus a disconnect; the two tables render through the shared
table macro; comms shows what the scheduled bill email would say.
"""

import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from tests.web.conftest import Ledger, Streams
from tests.web.dom import none, one, select, table_rows, text, triggers


def nav(page: str) -> list[str]:
    return [text(a) for a in select(page, "#settings-nav a")]


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both providers built in and credentialed."""
    for name, value in (
        ("FINANCE_PLAID", True),
        ("FINANCE_SNAPTRADE", True),
        ("PLAID_CLIENT_ID", "id"),
        ("PLAID_SECRET", "secret"),
        ("SNAPTRADE_CLIENT_ID", "id"),
        ("SNAPTRADE_CONSUMER_KEY", "key"),
    ):
        monkeypatch.setattr(settings, name, value)


class TestNav:
    def test_every_tab_is_a_sibling_route(self, client: TestClient) -> None:
        page = client.get("/settings").text
        assert nav(page) == [
            "Connections",
            "Categories",
            "Payees",
            "Institutions",
            "Comms",
        ]
        current = one(page, '#settings-nav a[aria-current="page"]')
        assert text(current) == "Connections"
        for path in (
            "/settings/categories",
            "/settings/payees",
            "/settings/institutions",
            "/settings/comms",
        ):
            marked = one(client.get(path).text, '#settings-nav a[aria-current="page"]')
            assert marked.get("href") == path

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/settings").text, "html")


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

    async def test_disconnecting_takes_the_card_with_it(
        self, client: TestClient, connection: int
    ) -> None:
        """The dialog closing is not enough: the card behind it would sit
        there offering a button that now 404s."""
        response = client.delete(f"/settings/connections/{connection}")
        assert json.loads(response.headers["HX-Location"])["path"] == "/settings"

    def test_unknown_connection_is_404(self, client: TestClient) -> None:
        assert client.delete("/settings/connections/999999").status_code == 404

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
