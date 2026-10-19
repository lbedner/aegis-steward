"""The Cash flow page (#344).

Income and spending by category for a range, the savings rate, and the
years side by side. Both render paths, selectors not substrings.
"""

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.routes.finance.cash_flow import SECTION
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.web.conftest import Ledger
from tests.web.dom import card, none, one, select, stat, text


class TestThePage:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the page before filing and after
    async def test_the_figures_and_the_rate(
        self,
        client: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        """$500.00 in, $72.00 out: $428.00 kept, 86% of what came in -
        once the payroll is filed as income. Filed as nothing, it is in
        neither, and said (#316)."""
        page = client.get(SECTION.path).text
        assert stat(page, "Income") == "$0.00"
        assert "$500.00" in text(one(page, "#unfiled-notice"))

        salary = await finance.get_or_create_category_from_hint("Income:Salary")
        rows, _total = await finance.list_transactions(owner_user_id=None)
        payroll = next(t for t in rows if t.name == "Payroll")
        payroll.category_id = salary.id
        async_db_session.add(payroll)
        await async_db_session.commit()
        page = client.get(SECTION.path).text

        assert stat(page, "Income") == "$500.00"
        assert stat(page, "Spending") == "$72.00"
        assert stat(page, "Saved") == "$428.00"
        assert stat(page, "Savings rate") == "86%"
        assert one(page, "#unfiled-notice").get("hidden") is not None

    def test_spending_by_category(self, client: TestClient, ledger: Ledger) -> None:
        rows = select(card(client.get(SECTION.path).text, "Spending by category"), "li")
        assert [text(select(r, "span")[0]) for r in rows] == ["Food", "Auto"]

    @pytest.mark.asyncio
    async def test_the_tables_start_the_day_the_totals_do(
        self, client: TestClient, finance: FinanceService, ledger: Ledger
    ) -> None:
        """A $3,000 paycheck 30 days back, the day before a 30-day
        window: the Income table counted it, Income did not."""
        salary = await finance.get_or_create_category_from_hint("Income:Salary")
        await finance.create_transaction(
            account_id=ledger.checking,
            amount=300_000,
            txn_date=current_date() - timedelta(days=30),
            name="Paycheck",
            category_id=salary.id,
        )
        await finance.db.commit()

        page = client.get(f"{SECTION.path}?days=30").text

        assert stat(page, "Income") == "$0.00"
        none(card(page, "Income by category"), "li")

    def test_the_years_side_by_side(self, client: TestClient, ledger: Ledger) -> None:
        table = card(client.get(SECTION.path).text, "By year")
        years = [text(select(row, "td")[0]) for row in select(table, "tbody tr")]
        assert years[-1] == str(current_date().year)

    def test_the_range_is_in_the_filter(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        form = one(client.get(SECTION.path).text, "form#filter")
        one(form, 'input[type=date][name="from"]')
        one(form, 'input[type=date][name="to"]')

    def test_fragment_has_no_shell(self, hx: TestClient, ledger: Ledger) -> None:
        fragment = hx.get(SECTION.path).text
        none(fragment, "html")
        assert stat(fragment, "Spending") == "$72.00"
