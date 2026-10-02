"""The Cash flow page (#344).

Income and spending by category for a range, the savings rate, and the
years side by side. Both render paths, selectors not substrings.
"""

from fastapi.testclient import TestClient

from app.components.web_frontend.routes.finance.cash_flow import SECTION
from app.services.finance.utils import current_date
from tests.web.conftest import Ledger
from tests.web.dom import card, none, one, select, stat, text


class TestThePage:
    def test_the_figures_and_the_rate(self, client: TestClient, ledger: Ledger) -> None:
        """$500.00 in, $72.00 out: $428.00 kept, 86% of what came in."""
        page = client.get(SECTION.path).text

        assert stat(page, "Income") == "$500.00"
        assert stat(page, "Spending") == "$72.00"
        assert stat(page, "Saved") == "$428.00"
        assert stat(page, "Savings rate") == "86%"

    def test_spending_by_category(self, client: TestClient, ledger: Ledger) -> None:
        rows = select(card(client.get(SECTION.path).text, "Spending by category"), "li")
        assert [text(select(r, "span")[0]) for r in rows] == ["Food", "Auto"]

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
        assert stat(fragment, "Income") == "$500.00"
