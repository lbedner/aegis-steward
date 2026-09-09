"""Projected: today's cash balance walked forward through the committed
bills and income, as a line with overdue markers and the ledger under
it, both from one projection call. The account filter narrows both."""

from datetime import date

from fastapi.testclient import TestClient

from app.components.web_frontend.filters import short_date
from tests.web.conftest import Ledger, Streams
from tests.web.dom import chart_data, none, one, select, stat, table_rows, text


class TestPage:
    def test_stats_chart_and_ledger_from_one_projection(
        self, client: TestClient, streams: Streams
    ) -> None:
        page = client.get("/projected?days=30").text
        # Cash today is checking + savings. In 30 days: two paydays (+5,000),
        # rent (-1,500), water overdue today and again in 20 days (-90).
        assert stat(page, "Today's balance") == "$150.00"
        assert stat(page, "Upcoming total") == "+$3,410.00"
        assert stat(page, "Projected balance") == "$3,560.00"

        data = chart_data(page, "line")
        assert len(data["labels"]) == 31 and data["labels"][0] == short_date(
            date.today()
        )
        balance, overdue = data["series"]
        assert balance["label"] == "Balance" and len(balance["values"]) == 31
        assert overdue["label"] == "Overdue" and overdue.get("points") is True
        # Water was due 10 days ago: it lands on today (index 0) as a marker.
        assert overdue["values"][0] == balance["values"][0]
        assert overdue["values"][1] is None

        rows = table_rows(page, "#projection table")
        assert list(rows[0]) == [
            "Date",
            "Name",
            "Category",
            "Account",
            "Amount",
            "Balance",
        ]
        assert [text(r["Name"]) for r in rows][:3] == ["Water", "Payroll", "Rent"]
        assert len(rows) == 5
        # The ledger wears the same brand mark as every other payee row.
        assert one(rows[0]["Name"], "[data-avatar]").get("data-avatar") == "W"
        when = one(rows[0]["Date"], "[data-tone]")
        assert when.get("data-tone") == "warn"  # shows the day it was due
        assert text(rows[1]["Amount"]) == "+$2,500.00"
        assert "text-aegis-teal" in (rows[1]["Amount"].get("class") or "")
        assert "text-error" in (rows[2]["Amount"].get("class") or "")
        assert text(rows[2]["Balance"]) == "$1,105.00"  # 150 - 45 + 2,500 - 1,500

    def test_account_filter_narrows_the_start(
        self, client: TestClient, ledger: Ledger, streams: Streams
    ) -> None:
        page = client.get("/projected").text
        form = one(page, "form#filter")
        assert form.get("hx-get") == "/projected"
        assert one(form, 'input[name="days"][checked]').get("value") == "90"
        # The same chip row as every other window, six months by default.
        assert [c.get("value") for c in select(form, 'input[name="days"]')] == [
            "1",
            "7",
            "14",
            "30",
            "90",
            "365",
            "9999",
        ]
        narrowed = client.get(f"/projected?account_ids={ledger.savings}").text
        assert stat(narrowed, "Today's balance") == "$50.00"
        none(narrowed, "#projection table")  # no bills draw on savings

    def test_all_asks_for_the_forecast_ceiling(
        self, client: TestClient, streams: Streams
    ) -> None:
        """A forward window has no "everything"; All is the horizon cap."""
        page = client.get("/projected?days=9999").text
        assert one(page, 'input[name="days"][checked]').get("value") == "9999"
        assert len(chart_data(page, "line")["labels"]) == 731  # the 730-day cap

    def test_fragment_and_empty_state(self, hx: TestClient, ledger: Ledger) -> None:
        fragment = hx.get("/projected").text
        none(fragment, "html")
        assert "Nothing scheduled" in text(one(fragment, "#projection"))
        none(fragment, "canvas")  # a flat line with no events is not a chart
        assert len(select(fragment, "dl dd")) == 3
