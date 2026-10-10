"""``data_table``: the one table macro in the app.

Columns are declared, rows are objects or dicts, and formatting goes
through the filters, so a page never hand-writes a ``<table>``.
"""

from dataclasses import dataclass
from datetime import date

from fastapi.testclient import TestClient

from app.components.web_frontend.rendering import templates
from app.services.finance.service import FinanceService
from tests.web.conftest import Ledger
from tests.web.dom import none, one, portfolio_row, select, text

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

    def test_the_visit_is_remembered(self, client: TestClient, ledger: Ledger) -> None:
        client.get(f"/accounts/{ledger.checking}")
        assert "seen_register" in client.cookies

    def test_every_ledger_is_marked_the_same_way(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        """Five ledgers, one mark. Each surface keeps its own watermark,
        so reading the register does not mark a document you have never
        opened as seen.

        Holdings and Activity share the register's, deliberately: they
        are two tables on one tab, seen in the same glance, and a second
        cookie would mark one of them unread while the reader was
        looking straight at it.
        """
        client.get(f"/accounts/{ledger.checking}")
        client.get(f"/accounts/{ledger.card}/overview")
        client.get(f"/accounts/{ledger.card}/documents")
        assert {"seen_register", "seen_valuations", "seen_documents"} <= set(
            client.cookies
        )

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
            return Request(
                {"type": "http", "headers": headers, "method": "GET", "path": "/"}
            )

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


class TestThePortfolioCarriesTheSameMark:
    """The account row says "something arrived here" with the register's
    own dot and the register's own watermark, so the two pages can never
    disagree about what is new. A first visit marks nothing, as before.
    """

    async def test_an_account_with_unseen_rows_is_marked(
        self, client: TestClient, finance: FinanceService, ledger: Ledger
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from app.services.finance.utils import current_date

        # Looked at the register long ago, then a row arrived at Checking.
        seen = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        client.cookies.set("seen_register", f"{seen}|{seen}")
        await finance.create_transaction(
            account_id=ledger.checking,
            amount=-1_000,
            txn_date=current_date(),
            name="NEW ARRIVAL",
        )
        page = client.get("/accounts").text
        assert portfolio_row(page, "Checking").find(".//*[@data-arrived]") is not None
        assert portfolio_row(page, "Savings").find(".//*[@data-arrived]") is None

    def test_a_first_visit_marks_no_account(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts").text
        assert not select(page, "#portfolio [data-arrived]")


class TestOnAPhone:
    """Below ``sm`` a row stacks: what it is and what it cost on top, the
    rest beneath with its label. The layout is CSS keyed on each cell's
    role, so every table gets it from the macro and none builds its own
    (issue 465)."""

    def _roles(self, html: str) -> list[str]:
        return [td.get("data-role") for td in select(html, "tbody td")]

    def test_what_the_row_is_leads_and_the_first_money_column_is_the_amount(
        self,
    ) -> None:
        """A date or a status says when or how a row stands, not what it
        is, so the lead passes over it."""
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Coffee", -450)])
        assert self._roles(html) == ["detail", "primary", "amount"]
        one(html, "table[data-table]")

    def test_a_detail_cell_carries_its_column_label(self) -> None:
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Coffee", -450)])
        assert one(html, "td[data-role=detail]").get("data-label") == "Date"

    def test_a_column_can_claim_the_lead(self) -> None:
        columns = [{**COLUMNS[0], "phone": "primary"}, COLUMNS[1], COLUMNS[2]]
        html = render(columns, [Row(date(2026, 7, 15), "Coffee", -450)])
        assert self._roles(html) == ["primary", "detail", "amount"]

    def test_a_column_can_claim_the_amount(self) -> None:
        columns = [
            {"key": "name", "label": "Name"},
            {"key": "income", "label": "Income", "kind": "money"},
            {"key": "saved", "label": "Saved", "kind": "money", "phone": "amount"},
        ]
        html = render(columns, [{"name": "2026", "income": 100, "saved": 40}])
        assert self._roles(html) == ["primary", "detail", "amount"]

    def test_the_register_row_declares_the_same_roles(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get(f"/accounts/{ledger.checking}").text
        row = select(page, "#register tbody tr")[0]
        roles = {td.get("data-role") for td in select(row, "td")}
        assert {"select", "primary", "amount", "actions"} <= roles
        one(row, "td[data-role=primary][data-cell=payee]")


class TestOverseerCells:
    """The kit's cells Overseer reads with: code, links, wrapping text,
    and a heading a column draws itself."""

    def test_a_column_may_render_its_own_header(self) -> None:
        template = templates.env.from_string(
            '{% from "components/macros/table.html" import data_table %}'
            '{% macro box() %}<input type="checkbox" data-all>{% endmacro %}'
            '{{ data_table([{"key": "name", "label": "Name", "header": box}], rows) }}'
        )
        html = template.render(rows=[{"name": "Coffee"}])
        assert select(html, "thead th input[data-all]")

    def test_code_is_monospace(self) -> None:
        columns = [{"key": "key", "label": "Key", "kind": "code"}]
        assert text(one(render(columns, [{"key": "cache:user:1"}]), "td code")) == (
            "cache:user:1"
        )

    def test_a_link_opens_its_item_in_the_target(self) -> None:
        columns = [
            {"key": "title", "label": "Title", "kind": "link", "target": "#main"}
        ]
        link = one(
            render(columns, [{"title": {"label": "Lease", "url": "/d?id=1"}}]), "td a"
        )
        assert text(link) == "Lease" and link.get("href") == "/d?id=1"
        assert link.get("hx-target") == "#main"

    def test_a_wrapping_column_wraps(self) -> None:
        columns = [{"key": "rule", "label": "Rule", "wrap": True}]
        cell = one(render(columns, [{"rule": "Host(`a`) && Path(`/b`)"}]), "tbody td")
        assert "whitespace-normal" in cell.get("class")


class TestDetailRows:
    SOURCE = (
        '{% from "components/macros/table.html" import data_table %}'
        '{% macro more(row) %}<p class="more">{{ row.name }} detail</p>{% endmacro %}'
        "{{ data_table(columns, rows, detail=more) }}"
    )

    def _render(self) -> str:
        rows = [
            Row(date(2026, 7, 15), "Coffee", -450),
            Row(date(2026, 7, 16), "Tea", -300),
        ]
        return templates.env.from_string(self.SOURCE).render(columns=COLUMNS, rows=rows)

    def test_each_row_carries_a_hidden_detail_row(self) -> None:
        html = self._render()
        details = select(html, "tr[data-detail]")
        assert [text(one(d, ".more")) for d in details] == [
            "Coffee detail",
            "Tea detail",
        ]
        assert all(d.get("x-show") == "open" for d in details)
        assert one(details[0], "td").get("colspan") == str(len(COLUMNS) + 1)

    def test_a_button_toggles_the_detail(self) -> None:
        html = self._render()
        buttons = select(html, "tbody button[aria-expanded]")
        assert len(buttons) == 2
        assert buttons[0].get("aria-expanded") == "false"

    def test_without_detail_there_is_no_toggle(self) -> None:
        html = render(COLUMNS, [Row(date(2026, 7, 15), "Coffee", -450)])
        none(html, "tr[data-detail]")
        none(html, "button[aria-expanded]")


class TestRenderedCells:
    def test_a_column_macro_renders_the_cell(self) -> None:
        source = (
            '{% from "components/macros/table.html" import data_table %}'
            '{% macro shout(value) %}<b class="shout">{{ value|upper }}</b>{% endmacro %}'
            '{{ data_table([{"key": "name", "label": "Name", "render": shout}], rows) }}'
        )
        html = templates.env.from_string(source).render(rows=[{"name": "tea"}])
        assert text(one(html, "td b.shout")) == "TEA"


class TestActionsArgument:
    """Actions can be passed as a macro, so one table serves viewers with and
    without them instead of being written twice."""

    SOURCE = (
        '{% from "components/macros/table.html" import data_table %}'
        '{% macro verbs(row) %}<button class="verb">{{ row.name }}</button>{% endmacro %}'
        "{{ data_table(columns, rows, actions=verbs if show else none) }}"
    )

    def _render(self, show: bool) -> str:
        rows = [Row(date(2026, 7, 15), "Coffee", -450)]
        return templates.env.from_string(self.SOURCE).render(
            columns=COLUMNS, rows=rows, show=show
        )

    def test_a_macro_adds_the_actions_column(self) -> None:
        assert text(one(self._render(True), "td button.verb")) == "Coffee"

    def test_none_leaves_it_out(self) -> None:
        html = self._render(False)
        none(html, "button.verb")
        assert len(select(html, "thead th")) == len(COLUMNS)


def test_a_group_row_heads_the_rows_it_holds() -> None:
    """A row marked ``group`` reads as a heading (``data-group``) and the
    ``child`` rows under it are indented, so a total and its parts share
    one table and one set of columns."""
    columns = [{"key": "name", "label": "Name"}, {"key": "n", "label": "N"}]
    html = render(
        columns,
        [
            {"name": "Chat", "n": 3, "group": True},
            {"name": "stream", "n": 2, "child": True},
            {"name": "plain", "n": 1, "child": True},
        ],
    )
    rows = select(html, "tbody tr")
    assert rows[0].get("data-group") is not None
    assert all(r.get("data-group") is None for r in rows[1:])
    assert "pl-10" in select(rows[1], "td")[0].get("class")
    assert "pl-10" not in select(rows[0], "td")[0].get("class")
