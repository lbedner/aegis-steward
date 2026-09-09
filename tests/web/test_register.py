"""The register: the transactions behind an account, filtered and paged.

It lives inside the account pages as ``#register``. Every filter is a
form field on the same URL, so search, filters, and paging are one
``hx-get`` that swaps the section in place and updates the URL.
"""

from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, table_rows, text


def names(markup: str, column: str = "Payee") -> list[str]:
    return [text(row[column]) for row in table_rows(markup, "#register table")]


class TestAllAccountsRegister:
    def test_lists_every_transaction_newest_first_with_the_account(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        heads = [text(th) for th in select(page, "#register thead th")]
        assert heads == [
            "Select",
            "Date",
            "Account",
            "Payee",
            "Category",
            "Amount",
            "Actions",
        ]
        assert names(page) == [
            "Market",
            "Market",
            "Gas",
            "Mystery charge",
            "Payroll",
        ]
        assert text(table_rows(page, "#register table")[0]["Account"]) == "Checking"


class TestAccountRegister:
    def test_scopes_to_the_account_and_drops_the_account_column(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        heads = [text(th) for th in select(page, "#register thead th")]
        assert heads == ["Select", "Date", "Payee", "Category", "Amount", "Actions"]
        assert names(page) == ["Market", "Market", "Payroll"]

    def test_rows_are_addressable(self, client: TestClient, ledger: Ledger) -> None:
        """Row actions (next ticket) swap a single row back by id."""
        page = client.get(f"/accounts/{ledger.checking}").text
        ids = [tr.get("id") or "" for tr in select(page, "#register tbody tr")]
        assert all(i.startswith("txn-") for i in ids) and len(set(ids)) == 3

    def test_uncategorised_row_says_so(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.card}").text
        cell = table_rows(page, "#register table")[1]["Category"]  # Mystery charge
        assert text(one(cell, "option[selected]")) == "Uncategorized"


class TestFilters:
    def test_form_re_renders_the_register_in_place(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        form = one(
            client.get(f"/accounts/{ledger.checking}").text, "form#register-filters"
        )
        assert form.get("hx-get") == f"/accounts/{ledger.checking}"
        assert form.get("hx-target") == "#register"
        assert form.get("hx-select") == "#register"
        assert form.get("hx-swap") == "outerHTML"
        assert form.get("hx-push-url") == "true"
        assert "keyup changed delay:300ms" in (form.get("hx-trigger") or "")

    def test_self_selecting_requests_replace_rather_than_nest(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Anything that selects the element it targets (filters, pager,
        account links) must swap outerHTML, or each click nests a copy."""
        page = client.get("/accounts?page_size=1").text
        selfish = [
            el
            for el in select(page, "[hx-select]")
            if el.get("hx-select") == el.get("hx-target")
        ]
        assert len(selfish) >= 4
        assert {el.get("hx-swap") for el in selfish} == {"outerHTML"}

    def test_payee_cell_carries_the_brand_icon(
        self,
        client: TestClient,
        ledger: Ledger,
        merchant: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A resolved icon rides the row; an unresolved payee shows its initial."""
        from app.services.finance.domains.ledger import merchant_icon

        monkeypatch.setitem(merchant_icon._CACHE, "shell.com", "AAAA")
        cells = select(client.get("/accounts").text, "[data-cell=payee]")
        shell = next(c for c in cells if "Shell" in text(c))
        assert one(shell, "img").get("src") == "/icons?key=shell.com"
        other = next(c for c in cells if "Market" in text(c))
        none(other, "img")
        assert one(other, "[data-avatar]").get("data-avatar") == "M"

    def test_search_narrows_by_payee(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get(f"/accounts/{ledger.checking}?q=Market").text
        assert names(page) == ["Market", "Market"]
        assert one(page, 'input[name="q"]').get("value") == "Market"

    def test_category_filter_offers_every_category_and_narrows(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        options = [
            text(o)
            for o in select(page, '#register-filters select[name="category_id"] option')
        ]
        assert options[0] == "All categories"
        assert "Food:Groceries" in options and "Auto:Fuel" in options
        groceries = next(
            o
            for o in select(page, 'select[name="category_id"] option')
            if text(o) == "Food:Groceries"
        )
        filtered = client.get(f"/accounts?category_id={groceries.get('value')}").text
        assert names(filtered) == ["Market", "Market"]
        assert one(
            filtered, '#register-filters select[name="category_id"] option[selected]'
        ).get("value") == groceries.get("value")

    def test_date_range_narrows(self, client: TestClient, ledger: Ledger) -> None:
        today = date.today().isoformat()
        page = client.get(f"/accounts?from={today}&to={today}").text
        assert names(page) == ["Market"]
        assert one(page, 'input[name="from"]').get("value") == today

    def test_transfers_toggle_is_a_checkbox_default_off(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        box = one(client.get("/accounts").text, 'input[name="include_transfers"]')
        assert box.get("type") == "checkbox" and box.get("checked") is None

    def test_a_submitted_form_with_blank_fields_is_fine(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        """The browser sends ``""`` for untouched selects and dates; ticking
        one box must not 422 the whole register."""
        response = hx.get(
            "/accounts?q=&category_id=&merchant_id=&from=&to=&include_transfers=on"
        )
        assert response.status_code == 200
        assert one(response.text, 'input[name="include_transfers"]').get("checked")

    def test_window_chips_start_at_all_and_narrow_the_listing(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The chips are the first control in the bar; the widest is the
        default, so a cold load hides nothing."""
        page = client.get("/accounts").text
        chips = select(page, "#register-filters input[name='days']")
        assert [c.get("value") for c in chips][:2] == ["1", "7"]
        assert (
            one(page, "#register-filters input[name='days'][checked]").get("value")
            == "9999"
        )
        assert len(names(page)) == 5

        # The ledger runs five days back; a one-day window keeps today and
        # yesterday.
        narrowed = client.get("/accounts?days=1").text
        assert names(narrowed) == ["Market", "Market"]
        assert (
            one(narrowed, "#register-filters input[name='days'][checked]").get("value")
            == "1"
        )

    def test_an_explicit_from_date_beats_the_window(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        today = date.today().isoformat()
        page = client.get(f"/accounts?days=9999&from={today}").text
        assert names(page) == ["Market"]

    def test_the_window_rides_the_pager_and_the_clear_link(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts?days=90&page_size=1").text
        assert "days=90" in (one(page, '#register nav a[rel="next"]').get("href") or "")
        one(page, "#register-filters a")  # narrowed, so Clear is offered

    def test_no_match_says_so(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/accounts?q=zzz").text
        none(page, "#register table")
        assert "No transactions match" in text(one(page, "#register"))


class TestPaging:
    def test_pager_counts_and_links_carry_the_filters(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts?page_size=1&q=Market").text
        pager = one(page, "#register nav[aria-label='Pagination']")
        assert "1-1 of 2" in text(pager)
        nxt = one(pager, 'a[rel="next"]')
        href = nxt.get("href") or ""
        assert nxt.get("hx-get") == href and nxt.get("hx-target") == "#register"
        assert "page=2" in href and "q=Market" in href and "page_size=1" in href
        none(pager, 'a[rel="prev"]')

    def test_second_page(self, client: TestClient, ledger: Ledger) -> None:
        page = client.get("/accounts?page_size=2&page=2").text
        assert names(page) == ["Gas", "Mystery charge"]
        pager = one(page, "#register nav[aria-label='Pagination']")
        assert "3-4 of 5" in text(pager)
        one(pager, 'a[rel="prev"]')
        one(pager, 'a[rel="next"]')

    def test_single_page_has_no_pager(self, client: TestClient, ledger: Ledger) -> None:
        none(client.get("/accounts").text, "#register nav[aria-label='Pagination']")


class TestInvestmentAccount:
    @pytest.fixture
    async def brokerage(
        self, finance: FinanceService, async_db_session: AsyncSession
    ) -> int:
        account = await finance.create_manual_account(
            name="Brokerage", account_type="brokerage", classification="asset"
        )
        assert account.id is not None
        vti = await finance.get_or_create_security(
            ticker="VTI", name="Vanguard Total Market"
        )
        assert vti.id is not None
        await finance.upsert_holding(
            owner_user_id=None,
            account_id=account.id,
            security_id=vti.id,
            as_of_date=date.today(),
            quantity_e8=10 * 10**8,
            price=25_000,
        )
        await finance.upsert_trade(
            owner_user_id=None,
            account_id=account.id,
            trade_type="buy",
            trade_date=date.today(),
            amount=-250_000,
            security_id=vti.id,
            quantity_e8=10 * 10**8,
            price=25_000,
        )
        await async_db_session.commit()
        return account.id

    def test_shows_holdings_and_trades_instead_of_a_register(
        self, client: TestClient, brokerage: int
    ) -> None:
        page = client.get(f"/accounts/{brokerage}").text
        none(page, "form#register-filters")
        holdings = one(page, "#holdings table")
        assert [text(th) for th in select(holdings, "thead th")] == [
            "Ticker",
            "Name",
            "Quantity",
            "Price",
            "Value",
        ]
        first = [text(td) for td in select(holdings, "tbody tr")[0]]
        assert first[0] == "VTI" and first[2] == "10" and first[4] == "$2,500.00"
        trades = one(page, "#trades table")
        assert [text(td) for td in select(trades, "tbody tr")[0]][1:3] == ["Buy", "VTI"]


class TestFragment:
    def test_register_fragment_has_no_shell(
        self, hx: TestClient, ledger: Ledger
    ) -> None:
        fragment = hx.get(f"/accounts/{ledger.checking}?q=Market").text
        none(fragment, "aside")
        one(fragment, "#register")
