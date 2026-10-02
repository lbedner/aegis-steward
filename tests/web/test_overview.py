"""The Overview page.

One in-process call to the composite overview handler, rendered both
ways. Seeds go through ``finance`` (the service on the test session) and
are committed before the page is requested, exactly as the API tests do.
"""

import calendar
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.routes.finance.overview import COMPARES, WORTH_VIEWS
from app.services.finance.domains.ledger import networth
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.web.conftest import Ledger, Streams
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

    def test_cards_carry_the_brand_marks(
        self, client: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Top payees, upcoming bills and the two transaction tables show
        the same avatar the register does: the icon when it resolved,
        the initial otherwise."""
        from app.services.finance.domains.ledger import merchant_icon

        monkeypatch.setitem(merchant_icon._CACHE, "market.com", "AAAA")
        page = client.get("/overview").text
        first = select(card(page, "Top payees"), ".ranked li")[0]
        assert one(first, "img").get("src") == "/icons?key=market.com"
        # Upcoming bills shares the ranked recipe; the seeded ledger has none.
        assert select(card(page, "Recent transactions"), "img, [data-avatar]")

    async def test_upcoming_bill_without_a_brand_shows_its_category_glyph(
        self, client: TestClient, finance: FinanceService, streams: Streams
    ) -> None:
        rent = await finance.get_or_create_category_from_hint("Rent And Utilities")
        await finance.db.commit()
        client.post(f"/bills/{streams.rent}/categorize", data={"category_id": rent.id})
        section = card(client.get("/overview").text, "Upcoming bills")
        rows = select(section, ".ranked li")
        rent = next(r for r in rows if "Rent" in text(r))
        assert one(rent, "[data-glyph]").get("data-glyph") == "Rent And Utilities"

    async def test_top_payee_without_a_brand_shows_its_category_glyph(
        self, client: TestClient, finance: FinanceService, ledger: Ledger
    ) -> None:
        """Top payees are grouped by name with no payee row behind them;
        the card still knows where each is filed and draws that glyph."""
        rows, _ = await finance.list_transactions(owner_user_id=None, page_size=50)
        rent = await finance.get_or_create_category_from_hint("Rent And Utilities")
        market = [t for t in rows if t.name == "Market"]
        payee = await finance.create_merchant("Market", owner_user_id=None)
        await finance.assign_merchant(
            [t.id for t in market], payee.id, owner_user_id=None
        )
        for txn in market:
            await finance.categorize_transaction(txn.id, rent.id, owner_user_id=None)
        await finance.db.commit()
        section = card(client.get("/overview").text, "Top payees")
        first = select(section, ".ranked li")[0]
        assert text(first).startswith("Market")
        assert one(first, "[data-glyph]").get("data-glyph") == "Rent And Utilities"

    async def test_uncategorized_row_borrows_its_payees_usual_glyph(
        self, client: TestClient, finance: FinanceService, ledger: Ledger, merchant: int
    ) -> None:
        """An unfiled charge from a payee that is normally filed under a
        category shows that category's glyph, not a letter."""
        transport = await finance.get_or_create_category_from_hint("Transportation")
        rows, _ = await finance.list_transactions(owner_user_id=None, page_size=50)
        gas = next(t for t in rows if t.name == "Gas")
        await finance.categorize_transaction(gas.id, transport.id, owner_user_id=None)
        unfiled = await finance.create_transaction(
            account_id=gas.account_id, amount=-4_000, txn_date=gas.date_, name="Gas"
        )
        await finance.assign_merchant([unfiled.id], merchant, owner_user_id=None)
        await finance.db.commit()

        section = card(client.get("/overview").text, "Uncategorized")
        # Found by its amount: the row is named after its PAYEE now, the
        # same as every other transaction list, and the payee here is
        # the merchant just assigned rather than the raw "Gas".
        row = next(r for r in select(section, "tbody tr") if "$40.00" in text(r))
        assert one(row, "[data-glyph]").get("data-glyph") == "Transportation"

    def test_top_payees(self, client: TestClient, ledger: Ledger) -> None:
        section = card(client.get("/overview").text, "Top payees")
        first = select(section, ".ranked li")[0]
        assert text(first).startswith("Market") and "$45.00" in text(first)
        assert "2x" in text(first)  # two grocery runs
        assert one(first, "[style]").get("style") == "width: 100%"


class TestNetWorthByComponent:
    """#343: the net worth card explains itself."""

    @pytest.fixture
    async def history(self, ledger: Ledger, async_db_session: AsyncSession) -> Ledger:
        """The ledger with its daily balances written, as the nightly job
        would, so the card has a line to split."""
        await networth.recompute_snapshots(async_db_session, owner_user_id=None)
        await async_db_session.commit()
        return ledger

    def test_the_views_and_the_house_toggle(
        self, client: TestClient, history: Ledger
    ) -> None:
        page = client.get("/overview").text
        chips = select(page, "#net-worth-view a")
        views = [chip for _key, chip in WORTH_VIEWS]
        assert [text(c) for c in chips[:-1]] == views  # then the house toggle
        assert text(one(page, "#net-worth-view a[aria-current]")) == views[0]
        for chip in chips:
            assert chip.get("hx-select") == "#net-worth"

    def test_assets_and_debts_are_two_lines(
        self, client: TestClient, history: Ledger
    ) -> None:
        page = client.get("/overview?worth=split").text
        series = chart_data(one(page, "#net-worth"), "line")["series"]
        assert [s["label"] for s in series] == ["Assets", "Debts"]
        assert (series[0]["values"][-1], series[1]["values"][-1]) == (150.0, 25.0)

    def test_by_type_says_what_each_group_did(
        self, client: TestClient, history: Ledger
    ) -> None:
        page = client.get("/overview?worth=type").text
        series = chart_data(one(page, "#net-worth"), "line")["series"]
        assert [s["label"] for s in series] == ["Banking", "Credit Cards"]
        rows = select(page, "#net-worth-change li")
        assert [text(select(r, "span")[0]) for r in rows] == [
            "Banking",
            "Credit Cards",
        ]

    def test_the_choice_rides_the_filter(
        self, client: TestClient, history: Ledger
    ) -> None:
        page = client.get("/overview?worth=type&house=out").text
        kept = {
            i.get("name"): i.get("value")
            for i in select(page, 'input[type=hidden][form="filter"]')
        }
        assert (kept["worth"], kept["house"]) == ("type", "out")
        # The house toggle is on, and clicking it puts the house back.
        one(page, '#net-worth-view a[aria-current][href*="house=in"]')


class TestSpendingPace:
    """#305: this month's spending by day against the usual month."""

    def test_this_month_runs_against_the_average_month(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/overview").text
        data = chart_data(one(page, "#pace"), "line")
        today = current_date()
        assert len(data["labels"]) == calendar.monthrange(today.year, today.month)[1]
        this_month, usual = data["series"]
        assert this_month["label"] == "This month"
        assert len(this_month["values"]) == today.day
        assert (usual["label"], usual["compare"]) == (COMPARES[0][2], True)

    def test_the_comparison_is_a_choice_that_stays(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/overview?compare=median").text

        median = next(name for key, _chip, name in COMPARES if key == "median")
        assert chart_data(one(page, "#pace"), "line")["series"][1]["label"] == median
        chips = select(page, "#pace-compare a")
        assert [text(c) for c in chips] == [chip for _key, chip, _name in COMPARES]
        assert one(page, "#pace-compare a[aria-current]").get("hx-select") == "#pace"
        # The range and account filter carry it, so changing them keeps it.
        kept = one(page, 'input[type=hidden][name="compare"][form="filter"]')
        assert kept.get("value") == "median"


class TestWhatMoved:
    """#346: the categories that moved most this month, each opening its rows."""

    def test_each_category_opens_this_months_rows(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        section = card(client.get("/overview").text, "What moved")
        rows = select(section, ".ranked li")
        groceries = next(r for r in rows if "Groceries" in text(r))
        opener = one(groceries, "button[hx-get]")
        assert opener.get("hx-get") == (
            f"/overview/spending?category=Food%3AGroceries&days={current_date().day}"
        )
        assert opener.get("hx-target") == "#dialog-body"


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

    def test_range_pills_are_the_one_chip_row_defaulting_to_three_months(
        self, client: TestClient
    ) -> None:
        page = client.get("/overview").text
        assert [c.get("value") for c in select(page, 'input[name="days"]')] == [
            "1",
            "7",
            "14",
            "30",
            "90",
            "365",
            "9999",
        ]
        assert one(page, 'input[name="days"]:checked').get("value") == "90"


class TestADateRange:
    """#342: the charts take a from-to range, kept in the URL."""

    def test_the_donut_stops_at_the_end_date(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Today's $30.00 of groceries falls after a range ending yesterday."""
        today = current_date()
        start, end = today - timedelta(days=5), today - timedelta(days=1)
        page = client.get(f"/overview?from={start}&to={end}").text

        data = chart_data(page, "doughnut")
        assert dict(zip(data["labels"], data["series"][0]["values"], strict=True)) == {
            "Auto": 20.0,
            "Food": 15.0,
        }
        drill = one(page, 'canvas[data-chart="doughnut"]').get("data-drilldown", "")
        assert f"to={end}" in drill and f"from={start}" in drill

    def test_the_dates_are_in_the_filter(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        today = current_date()
        start = today - timedelta(days=5)
        page = client.get(f"/overview?from={start}&to={today}").text

        form = one(page, "form#filter")
        assert one(form, 'input[type=date][name="from"]').get("value") == str(start)
        assert one(form, 'input[type=date][name="to"]').get("value") == str(today)
        none(form, 'input[name="days"]:checked')  # a picked range beats the chips

    async def test_net_worth_and_cash_flow_stop_at_the_end_date(
        self, client: TestClient, ledger: Ledger, async_db_session: AsyncSession
    ) -> None:
        await networth.recompute_snapshots(async_db_session, owner_user_id=None)
        await async_db_session.commit()
        today = current_date()
        end = today - timedelta(days=3)
        page = client.get(f"/overview?from={today - timedelta(days=20)}&to={end}").text

        labels = chart_data(one(page, "#net-worth"), "line")["labels"]
        assert labels[-1] == str(end)
        assert labels[0] == str(today - timedelta(days=20))


class TestSpendingDrilldown:
    def test_returns_the_transactions_behind_a_slice(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        fragment = hx.get("/overview/spending?category=Food&days=180").text
        none(fragment, "aside")
        assert text(one(fragment, "h2")) == "Food"
        names = [text(tr.getchildren()[1]) for tr in select(fragment, "tbody tr")]
        assert names == ["Market", "Market"]

    def test_a_row_is_shaped_like_every_other_transaction(
        self, hx: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reported live 2026-09-12: the dialog showed letter avatars
        where the register shows brand icons. It had built its own
        lighter shaping - category name and nothing else - beside
        ``hydrate_transactions``, whose own docstring says every surface
        that shows a transaction reads the same shape from there. A
        split row arrived without its lines for the same reason."""
        from app.services.finance.domains.ledger import merchant_icon

        monkeypatch.setitem(merchant_icon._CACHE, "market.com", "AAAA")

        fragment = hx.get("/overview/spending?category=Food&days=180").text

        assert select(fragment, "tbody tr td img"), "no brand icon in the dialog"


class TestPendingChangesBanner:
    def test_sits_above_the_page_header(self, client: TestClient) -> None:
        """First thing in the content area, before the title: Leonard
        wants a pending change to be the first thing read."""
        content = one(client.get("/overview").text, "#app-content")
        assert content.getchildren()[0].get("id") == "pending-changes"

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
            account_id=account.id, amount=-500, txn_date=current_date(), name="Cafe"
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


class TestTheExpectedLetterNotice:
    """ST-11: the next request, before it arrives, beside the deadlines -
    the banner is where a person already looks for what is coming."""

    def test_nothing_expected_draws_nothing(self, client: TestClient) -> None:
        page = client.get("/overview").text
        assert one(page, "#matter-expected").get("hidden") is not None

    @pytest.mark.asyncio
    async def test_a_letter_due_inside_sixty_days_is_on_the_overview(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        from datetime import timedelta

        from app.services.matters.matters import MatterService

        matters = MatterService(async_db_session)
        matter = await matters.open(title="Medicaid renewal", reference="OV-EXP-1")
        await matters.set_cadence(
            int(matter.id),
            cadence="annual",
            next_expected_on=current_date() + timedelta(days=30),
        )
        await async_db_session.flush()

        notice = one(client.get("/overview").text, "#matter-expected")
        assert notice.get("hidden") is None
        assert notice.get("href") == "/matters"
        assert "1 matter" in text(notice)


class TestTheDeadlineNotice:
    """ST-09: a deadline nags before it is late, where the reader already
    looks. The sidebar's dot only ever appeared once the day had passed,
    which is a post-mortem rather than a warning."""

    def test_nothing_due_draws_nothing(self, client: TestClient) -> None:
        page = client.get("/overview").text
        assert one(page, "#matter-deadlines").get("hidden") is not None

    @pytest.mark.asyncio
    async def test_a_deadline_in_the_window_is_on_the_overview(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        """Written through the session the overview reads. In the app
        that is one database; in a web test the matters routes open their
        own, so a request filed over HTTP is invisible here."""
        from datetime import timedelta

        from app.services.matters.matters import MatterService
        from app.services.matters.requests import RequestService

        matter = await MatterService(async_db_session).open(
            title="Medicaid renewal", reference="OV-DUE-1"
        )
        await RequestService(async_db_session).record(
            matter_id=matter.id,
            due_on=current_date() + timedelta(days=3),
            items=[{"asked": "Proof of gross monthly income"}],
        )
        await async_db_session.flush()

        notice = one(client.get("/overview").text, "#matter-deadlines")
        assert notice.get("hidden") is None
        assert notice.get("href") == "/matters"
        assert "1 request" in text(notice)
