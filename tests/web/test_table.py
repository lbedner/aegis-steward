"""``data_table``: the one table macro in the app.

Columns are declared, rows are objects or dicts, and formatting goes
through the filters, so a page never hand-writes a ``<table>``.
"""

from dataclasses import dataclass
from datetime import date

from fastapi.testclient import TestClient

from app.components.web_frontend.rendering import templates
from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, text

COLUMNS = [
    {"key": "when", "label": "Date", "kind": "date"},
    {"key": "name", "label": "Name"},
    {"key": "amount", "label": "Amount", "kind": "money", "align": "right"},
]


@dataclass
class Row:
    when: date
    name: str
    amount: int
    currency: str = "USD"


def render(columns: list[dict], rows: list, **kwargs: object) -> str:
    template = templates.env.from_string(
        '{% from "components/macros/table.html" import data_table %}'
        "{{ data_table(columns, rows, **kwargs) }}"
    )
    return template.render(columns=columns, rows=rows, kwargs=kwargs)


class TestDataTable:
    def test_headers_come_from_the_column_labels(self) -> None:
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Coffee", -450)])
        assert [text(th) for th in select(html, "thead th")] == [
            "Date",
            "Name",
            "Amount",
        ]

    def test_cells_are_formatted_by_kind(self) -> None:
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Coffee", -450)])
        cells = [text(td) for td in select(html, "tbody td")]
        assert cells[0].startswith("Jul 15")
        assert cells[1] == "Coffee"
        assert cells[2] == "-$4.50"

    def test_money_honours_the_row_currency(self) -> None:
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Tea", 250, currency="GBP")])
        assert text(select(html, "tbody td")[2]) == "£2.50"

    def test_right_aligned_columns_align_header_and_cells(self) -> None:
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Coffee", -450)])
        assert "text-right" in (select(html, "thead th")[2].get("class") or "")
        assert "text-right" in (select(html, "tbody td")[2].get("class") or "")

    def test_accepts_dict_rows(self) -> None:
        html = render(
            [{"key": "name", "label": "Name"}], [{"name": "Rent"}, {"name": "Gas"}]
        )
        assert [text(td) for td in select(html, "tbody td")] == ["Rent", "Gas"]

    def test_empty_rows_render_the_empty_state_instead_of_a_table(self) -> None:
        html = render(COLUMNS, [], empty="No transactions yet")
        none(html, "table")
        assert text(one(html, "h2")) == "No transactions yet"

    def test_empty_has_a_default_title(self) -> None:
        html = render(COLUMNS, [])
        assert text(one(html, "h2")) == "Nothing here yet"

    def test_blank_values_render_as_a_dash(self) -> None:
        html = render([{"key": "name", "label": "Name"}], [{"name": None}])
        assert text(one(html, "tbody td")) == "-"


class TestAvatarCell:
    def test_avatar_kind_puts_the_brand_beside_the_name(self) -> None:
        """An ``avatar`` column shows the row's icon (or the initial when
        there is none) before the value, the way the Overseer rows did."""
        columns = [{"key": "name", "label": "Name", "kind": "avatar"}]
        html = render(
            columns,
            [
                {"id": 1, "name": "Shell", "icon_url": "/icons?key=shell.com"},
                {"id": 2, "name": "Water Co", "icon_url": None},
                {
                    "id": 3,
                    "name": "Landlord",
                    "icon_url": None,
                    "category": "Rent And Utilities",
                },
            ],
        )
        cells = select(html, "tbody td")
        img = one(cells[0], "img")
        assert img.get("src") == "/icons?key=shell.com" and img.get("alt") == ""
        assert text(cells[0]) == "Shell"
        none(cells[1], "img")
        assert one(cells[1], "[data-avatar]").get("data-avatar") == "W"
        assert text(cells[1]) == "Water Co"
        # A category with no brand shows the category's glyph, not a letter.
        assert one(cells[2], "[data-glyph] svg path").get("d")
        none(cells[2], "[data-avatar]")
        assert text(cells[2]) == "Landlord"


class TestTheRegisterIsATableLikeAnyOther:
    """The register hand-rolled its own ``<table>`` because it needed
    selection, sortable headers and a row that is an editing surface
    rather than a list of cells. Two implementations means every table
    feature is built twice or built wrong once - and the mark for "this
    arrived since you last looked" would have landed in the macro and
    missed the one ledger where it matters most."""

    def test_the_register_writes_no_table_markup(self) -> None:
        from pathlib import Path

        markup = Path(
            "app/components/web_frontend/templates/components/register.html"
        ).read_text()
        for tag in ("<table", "<thead", "<tbody", "<tr ", "<th "):
            assert tag not in markup, f"{tag} is the kit's job now"

    def test_selection_and_sorting_are_the_macro_s(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        # the chrome the register needs, rendered by the macro
        one(page, "#register th input[data-select-all]")
        sortable = select(page, "#register th a[href*='sort=']")
        assert sortable, "headers sort"
        # Every sortable header states its state; the select-all and the
        # actions columns are headers too and sort by nothing.
        assert len(select(page, "#register thead th[aria-sort]")) == len(sortable)

    def test_the_row_is_still_the_register_s_own(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """The kit owns the chrome; the caller owns the row. A column
        declaration cannot describe an inline category select."""
        page = client.get(f"/accounts/{ledger.checking}").text
        assert select(page, "#register tbody tr select[name=category_id]")


class TestWhatArrivedSinceYouLooked:
    """The mark means "you have not seen this", not "this is recent".

    By age it would re-mark the whole ledger after every import and could
    never be cleared: after a nightly Quicken run, 54 rows glow whether
    or not they have been read. A watermark clears because you looked.
    """

    def test_a_first_visit_marks_nothing(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """A ledger that lights up entirely on a first look has told the
        reader nothing."""
        page = client.get(f"/accounts/{ledger.checking}").text
        assert not select(page, "#register tbody .sr-only")

    def test_the_visit_is_remembered(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        client.get(f"/accounts/{ledger.checking}")
        assert "seen_register" in client.cookies

    def test_refreshing_does_not_clear_the_marks(self) -> None:
        """Seeing a list is not reading it. A highlight that vanishes on a
        refresh is one you cannot come back to, so the watermark advances
        only after a gap away."""
        from datetime import UTC, datetime, timedelta

        from starlette.requests import Request
        from starlette.responses import Response

        from app.components.web_frontend.seen import AWAY, remember, watermark

        first = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

        def request_with(cookie: str | None) -> Request:
            headers = [(b"cookie", cookie.encode())] if cookie else []
            return Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})

        # First visit: nothing seen before, so nothing is marked.
        response = remember(request_with(None), Response(), "register", first)
        jar = response.headers["set-cookie"].split(";")[0]
        assert watermark(request_with(jar), "register") == first

        # A refresh a minute later keeps the same watermark.
        soon = first + timedelta(minutes=1)
        response = remember(request_with(jar), Response(), "register", soon)
        jar2 = response.headers["set-cookie"].split(";")[0]
        assert watermark(request_with(jar2), "register") == first

        # Coming back after the gap advances it: the last visit is read.
        later = soon + AWAY + timedelta(minutes=1)
        response = remember(request_with(jar2), Response(), "register", later)
        jar3 = response.headers["set-cookie"].split(";")[0]
        assert watermark(request_with(jar3), "register") == soon
