"""``data_table``: the one table macro in the app.

Columns are declared, rows are objects or dicts, and formatting goes
through the filters, so a page never hand-writes a ``<table>``.
"""

from dataclasses import dataclass
from datetime import date

from app.components.web_frontend.rendering import templates
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
            ],
        )
        cells = select(html, "tbody td")
        img = one(cells[0], "img")
        assert img.get("src") == "/icons?key=shell.com" and img.get("alt") == ""
        assert text(cells[0]) == "Shell"
        none(cells[1], "img")
        assert one(cells[1], "[data-avatar]").get("data-avatar") == "W"
        assert text(cells[1]) == "Water Co"
