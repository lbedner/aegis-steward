"""Layout macros: card, stat_tile, chart_panel, dialog."""

import json

from fastapi.testclient import TestClient

from app.components.web_frontend.rendering import templates
from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, text

IMPORT = (
    '{% from "components/macros/layout.html" '
    "import card, stat_tile, chart_panel, dialog, tab_bar, tab_item, chip, page_header, figures, stats_strip, ranked_rows %}"
)


def render(source: str, **context: object) -> str:
    return templates.env.from_string(IMPORT + source).render(**context)


class TestCard:
    def test_is_a_titled_section(self) -> None:
        html = render('{% call card("Top payees") %}<p id="body">x</p>{% endcall %}')
        section = one(html, "section")
        heading = one(section, "h2")
        assert text(heading) == "Top payees"
        assert section.get("aria-labelledby") == heading.get("id")
        one(section, "#body")

    def test_optional_subtitle(self) -> None:
        html = render('{% call card("Spending", "last 90 days") %}x{% endcall %}')
        assert "last 90 days" in text(one(html, "section p"))


class TestStatTile:
    def test_is_a_definition_pair(self) -> None:
        html = render('{{ stat_tile("Assets", "$100.00") }}')
        assert text(one(html, "dt")) == "Assets"
        assert text(one(html, "dd")) == "$100.00"

    def test_negative_tone_marks_the_value(self) -> None:
        """Red once a figure goes negative; nothing for healthy figures,
        so the one number in trouble stands out."""
        bad = render('{{ stat_tile("Net worth", "-$5.00", negative=True) }}')
        good = render('{{ stat_tile("Net worth", "$5.00") }}')
        assert "text-error" in (one(bad, "dd").get("class") or "")
        assert "text-error" not in (one(good, "dd").get("class") or "")


class TestChartPanel:
    DATA = {"labels": ["Jan", "Feb"], "series": [{"label": "Net", "values": [1, 2]}]}

    def test_canvas_declares_its_kind_and_carries_json_data(self) -> None:
        html = render(
            '{{ chart_panel("nw", "Net worth", "line", data) }}', data=self.DATA
        )
        canvas = one(html, "canvas[data-chart]")
        assert canvas.get("data-chart") == "line"
        payload = one(html, 'script[type="application/json"]')
        assert payload.get("id") == canvas.get("data-chart-data")
        assert json.loads(payload.text or "") == self.DATA

    def test_is_a_titled_card(self) -> None:
        html = render(
            '{{ chart_panel("nw", "Net worth", "line", data) }}', data=self.DATA
        )
        assert text(one(html, "section h2")) == "Net worth"

    def test_drilldown_url_rides_the_canvas(self) -> None:
        html = render(
            '{{ chart_panel("sp", "Spending", "doughnut", data, drilldown="/overview/spending?days=90") }}',
            data=self.DATA,
        )
        assert one(html, "canvas").get("data-drilldown") == "/overview/spending?days=90"

    def test_no_executable_script(self) -> None:
        html = render(
            '{{ chart_panel("nw", "Net worth", "line", data) }}', data=self.DATA
        )
        none(html, 'script:not([type="application/json"])')


class TestDialog:
    def test_is_a_native_dialog_with_a_swap_target(self) -> None:
        html = render("{{ dialog() }}")
        dialog = one(html, "dialog#dialog")
        one(dialog, "#dialog-body")
        assert one(dialog, "button[aria-label='Close']") is not None

    def test_mounted_once_by_the_app_shell(self) -> None:
        page = templates.env.from_string(
            '{% extends "layouts/app_shell.html" %}{% block app_content %}x{% endblock %}'
        ).render()
        one(page, "dialog#dialog")
        none(one(page, "main#app-content"), "dialog")
        assert len(select(page, "dialog")) == 1


class TestTabsAndChips:
    def test_tabs_are_an_underlined_tablist(self) -> None:
        html = render(
            '{% call tab_bar("Views", id="v") %}'
            '{{ tab_item("One", True, "/one") }}{{ tab_item("Two", False, "/two") }}'
            "{% endcall %}"
        )
        nav = one(html, "nav#v[role=tablist]")
        assert "border-b" in nav.get("class")
        links = select(nav, "a")
        assert links[0].get("aria-current") == "page"
        assert "border-aegis-teal" in links[0].get("class")
        assert links[1].get("aria-current") is None
        assert "border-transparent" in links[1].get("class")

    def test_chip_is_the_one_chip_class(self) -> None:
        """Every chip row wears ``chip`` (input.css); a link marks the
        current choice with aria-current, and that is all it says."""
        active = one(render('{{ chip("30d", True, "/x?days=30") }}'), "a")
        idle = one(render('{{ chip("90d", False, "/x?days=90") }}'), "a")
        assert active.get("class") == "chip" and active.get("aria-current") == "page"
        assert idle.get("class") == "chip" and idle.get("aria-current") is None


class TestPageHeader:
    def test_title_subtitle_and_the_right_hand_side(self) -> None:
        html = render(
            '{% call page_header("Budget", "the month") %}<span id="r">x</span>{% endcall %}'
        )
        header = one(html, "header")
        assert text(one(header, "h1")) == "Budget" and "the month" in text(header)
        one(header, "#r")

    def test_figures_are_a_definition_list_the_stat_helper_reads(self) -> None:
        html = render(
            '{{ figures([{"label": "Assets", "value": "$1.00", "negative": False}, {"label": "Owed", "value": "-$2.00", "negative": True}]) }}'
        )
        assert text(select(html, "dl dt")[0]) == "Assets"
        assert "text-error" in select(html, "dd")[1].get("class")


class TestStatsStrip:
    def test_one_strip_with_a_cell_per_term(self) -> None:
        cells = [
            {
                "label": "Income",
                "value": "$10.00",
                "caption": "2 sources",
                "tone": None,
                "attrs": 'hx-get="/x/income"',
            },
            {
                "label": "This month",
                "value": "-$1.00",
                "caption": "short",
                "tone": "error",
                "attrs": "",
            },
        ]
        html = render("{{ stats_strip(cells, id='s') }}", cells=cells)
        strip = one(html, "dl#s")
        assert len(select(strip, "dt")) == 2
        assert one(strip, "[hx-get]").get("hx-get") == "/x/income"
        assert "text-error" in select(strip, "dd")[2].get("class")


class TestRankedRows:
    def test_bar_scales_to_the_ratio(self) -> None:
        rows = [
            {
                "label": "Market",
                "count": "2x",
                "value": "$45.00",
                "ratio": 1.0,
                "tone": "teal",
            },
            {
                "label": "Gas",
                "count": "1x",
                "value": "$20.00",
                "ratio": 0.444,
                "tone": "teal",
            },
        ]
        html = render("{{ ranked_rows(rows) }}", rows=rows)
        items = select(html, ".ranked li")
        assert len(items) == 2 and "Market" in text(items[0])
        assert one(items[1], "[style]").get("style") == "width: 44%"


class TestTheAccountFilterIsSectioned:
    """Fifteen accounts in one flat list made the reader scan for a name
    whose KIND they already knew."""

    def test_groups_are_in_ledger_order_and_empty_ones_are_dropped(self) -> None:
        from types import SimpleNamespace

        from app.services.finance.constants import account_sections

        accounts = [
            SimpleNamespace(id=1, name="Card", account_type="credit_card"),
            SimpleNamespace(id=2, name="Checking", account_type="checking"),
            SimpleNamespace(id=3, name="House", account_type="property"),
        ]

        sections = account_sections(accounts)

        assert [label for label, _ in sections] == [
            "Banking",
            "Credit Cards",
            "Property",
        ]
        assert [a.name for a in sections[0][1]] == ["Checking"]

    def test_the_filter_renders_a_heading_per_group(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/overview").text

        labels = [text(p) for p in select(page, "#filter details p")]

        assert "Banking" in labels
        assert len(labels) == len(set(labels)), "a group is listed once"
