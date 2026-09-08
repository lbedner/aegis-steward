"""The Overview page.

One in-process call to the composite overview handler, rendered both
ways. Seeds go through ``finance`` (the service on the test session) and
are committed before the page is requested, exactly as the API tests do.
"""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.web.conftest import Ledger
from tests.web.dom import card, chart_data, none, one, select, stat, text


class TestEmptyLedger:
    def test_renders_zero_stats_and_empty_states(self, client: TestClient) -> None:
        page = client.get("/overview").text
        assert stat(page, "Assets") == "$0.00"
        assert stat(page, "Liabilities") == "$0.00"
        assert stat(page, "Net worth") == "$0.00"
        none(page, "canvas")
        none(page, "table")
        assert select(page, "h2"), "empty states name what is missing"

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        fragment = hx.get("/overview").text
        none(fragment, "aside")
        assert stat(fragment, "Assets") == "$0.00"


class TestSeededLedger:
    def test_stat_tiles_sum_the_accounts(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/overview").text
        assert stat(page, "Assets") == "$150.00"
        assert stat(page, "Liabilities") == "-$25.00"
        assert stat(page, "Net worth") == "$125.00"

    def test_spending_donut_groups_by_parent_category(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        data = chart_data(client.get("/overview").text, "doughnut")
        assert data["labels"] == ["Food", "Auto"]
        assert data["series"][0]["values"] == [45.0, 20.0]

    def test_donut_click_drills_into_a_dialog(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/overview").text
        canvas = one(page, 'canvas[data-chart="doughnut"]')
        assert canvas.get("data-drilldown", "").startswith("/overview/spending?")

    def test_cashflow_bars_carry_income_and_spending(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        data = chart_data(client.get("/overview").text, "bar")
        labels = [s["label"] for s in data["series"]]
        assert labels == ["Income", "Spending"]
        assert sum(data["series"][0]["values"]) == 500.0
        assert sum(data["series"][1]["values"]) == 72.0

    def test_recent_transactions_table(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        table = one(card(client.get("/overview").text, "Recent transactions"), "table")
        names = [text(tr.getchildren()[1]) for tr in select(table, "tbody tr")]
        assert names[0] == "Market"
        assert len(names) == 5

    def test_uncategorized_preview_links_to_review(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        section = card(client.get("/overview").text, "Uncategorized")
        rows = select(section, "tbody tr")
        assert [text(tr.getchildren()[1]) for tr in rows] == [
            "Mystery charge",
            "Payroll",
        ]
        assert one(section, 'a[href="/review/uncategorized"]') is not None

    def test_top_payees(self, client: TestClient, ledger: Ledger) -> None:
        section = card(client.get("/overview").text, "Top payees")
        first = select(section, "tbody tr")[0]
        assert text(first.getchildren()[0]) == "Market"
        assert text(first.getchildren()[1]) == "$45.00"


class TestFilters:
    def test_account_filter_narrows_the_windowed_figures(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/overview?account_ids={ledger.checking}").text
        data = chart_data(page, "doughnut")
        assert data["labels"] == ["Food"]
        checked = select(page, 'input[name="account_ids"]:checked')
        assert [c.get("value") for c in checked] == [str(ledger.checking)]

    def test_filter_form_re_renders_the_section_in_place(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        form = one(client.get("/overview").text, "form#filter")
        assert form.get("hx-get") == "/overview"
        assert form.get("hx-target") == "#app-content"
        assert form.get("hx-push-url") == "true"
        assert form.get("hx-trigger") == "change"

    def test_range_pills_default_to_180_days(self, client: TestClient) -> None:
        page = client.get("/overview").text
        checked = one(page, 'input[name="days"]:checked')
        assert checked.get("value") == "180"


class TestSpendingDrilldown:
    def test_returns_the_transactions_behind_a_slice(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        fragment = hx.get("/overview/spending?category=Food&days=180").text
        none(fragment, "aside")
        assert text(one(fragment, "h2")) == "Food"
        names = [text(tr.getchildren()[1]) for tr in select(fragment, "tbody tr")]
        assert names == ["Market", "Market"]


class TestPendingChangesBanner:
    def test_hidden_when_nothing_is_pending(self, client: TestClient) -> None:
        """Present but hidden, so a resolution elsewhere can re-send it
        out of band and land."""
        banner = one(client.get("/overview").text, "#pending-changes")
        assert banner.get("hidden") is not None

    async def test_counts_pending_changes_and_links_to_review(
        self,
        client: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
    ) -> None:
        account = await finance.create_manual_account(
            name="Checking", account_type="checking", classification="asset"
        )
        txn = await finance.create_transaction(
            account_id=account.id, amount=-500, txn_date=date.today(), name="Cafe"
        )
        category = await finance.get_or_create_category_from_hint("Food:Coffee")
        assert category is not None
        await finance.propose_change(
            "transaction.categorize",
            {"transaction_id": txn.id, "category_id": category.id},
        )
        await async_db_session.commit()

        banner = one(client.get("/overview").text, "#pending-changes")
        assert banner.get("hidden") is None
        assert text(banner) == "1 change awaiting your review"
        assert banner.get("href") == "/review"
