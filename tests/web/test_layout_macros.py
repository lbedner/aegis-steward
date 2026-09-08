"""Layout macros: card, stat_tile, chart_panel, dialog."""

import json

from app.components.web_frontend.rendering import templates
from tests.web.dom import none, one, select, text

IMPORT = (
    '{% from "components/macros/layout.html" '
    "import card, stat_tile, chart_panel, dialog %}"
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
