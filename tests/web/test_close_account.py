"""Closing an account (#315): the page can do it, and it stops counting.

Hide and close existed only on the API, and ``is_closed`` was never read:
an account the bank closed years ago stayed in every total and every
picker. Now the account's Manage menu closes it (keeping its history,
leaving net worth from its close date) or hides it, and the Accounts
page keeps each in a section of its own, out of the total.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest

from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, stat, text


def _menu(client: TestClient, account_id: int) -> dict[str, str]:
    page = client.get(f"/accounts/{account_id}").text
    return {
        text(li): one(li, "button").get("hx-get") or ""
        for li in select(page, "#manage-menu li")
    }


class TestClose:
    @pytest.mark.queryspy(threshold=3)  # each page redraws its accounts
    def test_closed_it_leaves_the_total_the_pickers_and_the_overview(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        assert _menu(client, ledger.savings)["Close"] == (
            f"/accounts/{ledger.savings}/close"
        )
        closed_on = (current_date() - timedelta(days=10)).isoformat()

        hx.post(f"/accounts/{ledger.savings}/close", data={"closed_on": closed_on})

        page = client.get("/accounts").text
        assert "$75.00" in text(one(page, "[data-net-worth]"))
        none(page, f'#portfolio a[href="/accounts/{ledger.savings}/overview"]')
        closed = one(page, "[data-closed]")
        assert "Savings" in text(closed)
        assert "Closed" in text(closed)
        register = client.get("/accounts/all").text
        assert "Savings" not in [text(label) for label in select(register, "label")]
        assert stat(client.get("/overview").text, "Net worth") == "$75.00"

    @pytest.mark.queryspy(threshold=3)  # closed, then reopened: two redraws
    def test_it_still_opens_and_can_be_reopened(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        hx.post(f"/accounts/{ledger.savings}/close", data={})

        menu = _menu(client, ledger.savings)

        assert "Close" not in menu
        hx.post(f"/accounts/{ledger.savings}/reopen")
        assert "$125.00" in text(one(client.get("/accounts").text, "[data-net-worth]"))

    def test_the_close_dialog_says_what_closing_keeps(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        form = hx.get(f"/accounts/{ledger.savings}/close").text

        assert one(form, "input[name=closed_on]").get("value") == (
            current_date().isoformat()
        )
        assert "history" in text(one(form, "[data-explains]"))


class TestHide:
    @pytest.mark.queryspy(threshold=4)  # three changes, each redraws the line
    def test_hidden_reads_differently_from_closed(
        self, client: TestClient, hx: TestClient, ledger: Ledger
    ) -> None:
        hx.post(f"/accounts/{ledger.savings}/hide")
        hx.post(f"/accounts/{ledger.checking}/close", data={})

        page = client.get("/accounts").text

        assert "Savings" in text(one(page, "[data-hidden-accounts]"))
        assert "Savings" not in text(one(page, "[data-closed]"))
        assert "-$25.00" in text(one(page, "[data-net-worth]"))
        assert "Show" in _menu(client, ledger.savings)
        hx.post(f"/accounts/{ledger.savings}/show")
        none(client.get("/accounts").text, "[data-hidden-accounts]")

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the totals, before and after
    async def test_hidden_its_spending_leaves_the_totals_too(
        self, hx: TestClient, finance: FinanceService, ledger: Ledger
    ) -> None:
        """The dialog says it leaves every total, history and all: the
        Visa's $27.00 of charges left net worth and stayed in spending."""
        start = current_date() - timedelta(days=10)
        before = await finance.cash_flow(owner_user_id=None, start=start)

        hx.post(f"/accounts/{ledger.card}/hide")

        after = await finance.cash_flow(owner_user_id=None, start=start)
        assert (before.spending, after.spending) == (7_200, 4_500)

    @pytest.mark.asyncio
    async def test_a_goal_pot_is_not_offered_to_unhide(
        self, client: TestClient, finance: FinanceService, ledger: Ledger
    ) -> None:
        """Its money is already counted where it sits; shown, twice."""
        pot = await finance.create_manual_account(
            name="Close Test Pot", account_type="goal", classification="asset"
        )
        assert pot.id is not None
        await finance.update_account(pot.id, is_hidden=True)
        await finance.db.commit()

        none(client.get("/accounts").text, "[data-hidden-accounts]")
