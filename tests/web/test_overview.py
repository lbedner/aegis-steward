"""The Overview page.

One in-process call to the composite overview handler, rendered both
ways. Seeds go through ``finance`` (the service on the test session) and
are committed before the page is requested, exactly as the API tests do.
"""

from datetime import date, timedelta
import json

from fastapi.testclient import TestClient
from lxml.html import HtmlElement
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.web.dom import none, one, select, text


def stat(page: str, label: str) -> str:
    """The value shown under a stat tile label."""
    for dt in select(page, "dl dt"):
        if text(dt) == label:
            return text(dt.getnext())
    raise AssertionError(f"no stat tile {label!r}")


def chart_data(page: str, kind: str) -> dict:
    canvas = one(page, f'canvas[data-chart="{kind}"]')
    return json.loads(one(page, f"#{canvas.get('data-chart-data')}").text or "")


def card(page: str, title: str) -> HtmlElement:
    for section in select(page, "section"):
        headings = select(section, "h2")
        if headings and text(headings[0]) == title:
            return section
    raise AssertionError(f"no card {title!r}")


@pytest.fixture
async def ledger(finance: FinanceService, async_db_session: AsyncSession) -> dict:
    """A checking account and a card, three categorised outflows this
    month, one uncategorised, one deposit."""
    checking = await finance.create_manual_account(
        name="Checking", account_type="checking", classification="asset"
    )
    card_ = await finance.create_manual_account(
        name="Visa", account_type="credit_card", classification="liability"
    )
    await finance.update_account_balance(checking.id, current_balance=10_000)
    await finance.update_account_balance(card_.id, current_balance=-2_500)
    groceries = await finance.get_or_create_category_from_hint("Food:Groceries")
    fuel = await finance.get_or_create_category_from_hint("Auto:Fuel")
    today = date.today()
    rows = [
        ("Market", -3_000, groceries, checking),
        ("Market", -1_500, groceries, checking),
        ("Gas", -2_000, fuel, card_),
        ("Mystery charge", -700, None, card_),
        ("Payroll", 50_000, None, checking),
    ]
    for offset, (name, amount, category, account) in enumerate(rows):
        txn = await finance.create_transaction(
            account_id=account.id,
            amount=amount,
            txn_date=today - timedelta(days=offset),
            name=name,
        )
        txn.category_id = category.id if category is not None else None
        async_db_session.add(txn)
    await async_db_session.commit()
    return {"checking": checking.id, "card": card_.id}


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
        self, client: TestClient, ledger: dict
    ) -> None:
        page = client.get("/overview").text
        assert stat(page, "Assets") == "$100.00"
        assert stat(page, "Liabilities") == "-$25.00"
        assert stat(page, "Net worth") == "$75.00"

    def test_spending_donut_groups_by_parent_category(
        self, client: TestClient, ledger: dict
    ) -> None:
        data = chart_data(client.get("/overview").text, "doughnut")
        assert data["labels"] == ["Food", "Auto"]
        assert data["series"][0]["values"] == [45.0, 20.0]

    def test_donut_click_drills_into_a_dialog(
        self, client: TestClient, ledger: dict
    ) -> None:
        page = client.get("/overview").text
        canvas = one(page, 'canvas[data-chart="doughnut"]')
        assert canvas.get("data-drilldown", "").startswith("/overview/spending?")

    def test_cashflow_bars_carry_income_and_spending(
        self, client: TestClient, ledger: dict
    ) -> None:
        data = chart_data(client.get("/overview").text, "bar")
        labels = [s["label"] for s in data["series"]]
        assert labels == ["Income", "Spending"]
        assert sum(data["series"][0]["values"]) == 500.0
        assert sum(data["series"][1]["values"]) == 72.0

    def test_recent_transactions_table(self, client: TestClient, ledger: dict) -> None:
        table = one(card(client.get("/overview").text, "Recent transactions"), "table")
        names = [text(tr.getchildren()[1]) for tr in select(table, "tbody tr")]
        assert names[0] == "Market"
        assert len(names) == 5

    def test_uncategorized_preview_links_to_review(
        self, client: TestClient, ledger: dict
    ) -> None:
        section = card(client.get("/overview").text, "Uncategorized")
        rows = select(section, "tbody tr")
        assert [text(tr.getchildren()[1]) for tr in rows] == [
            "Mystery charge",
            "Payroll",
        ]
        assert one(section, 'a[href="/review/uncategorized"]') is not None

    def test_top_payees(self, client: TestClient, ledger: dict) -> None:
        section = card(client.get("/overview").text, "Top payees")
        first = select(section, "tbody tr")[0]
        assert text(first.getchildren()[0]) == "Market"
        assert text(first.getchildren()[1]) == "$45.00"


class TestFilters:
    def test_account_filter_narrows_the_windowed_figures(
        self, client: TestClient, ledger: dict
    ) -> None:
        page = client.get(f"/overview?account_ids={ledger['checking']}").text
        data = chart_data(page, "doughnut")
        assert data["labels"] == ["Food"]
        checked = select(page, 'input[name="account_ids"]:checked')
        assert [c.get("value") for c in checked] == [str(ledger["checking"])]

    def test_filter_form_re_renders_the_section_in_place(
        self, client: TestClient, ledger: dict
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
        self, hx: TestClient, ledger: dict
    ) -> None:
        fragment = hx.get("/overview/spending?category=Food&days=180").text
        none(fragment, "aside")
        assert text(one(fragment, "h2")) == "Food"
        names = [text(tr.getchildren()[1]) for tr in select(fragment, "tbody tr")]
        assert names == ["Market", "Market"]


class TestPendingChangesBanner:
    def test_hidden_when_nothing_is_pending(self, client: TestClient) -> None:
        none(client.get("/overview").text, "#pending-changes")

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
        assert text(banner) == "1 change awaiting your review"
        assert banner.get("href") == "/review"
