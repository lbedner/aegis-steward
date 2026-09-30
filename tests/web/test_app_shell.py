"""The app shell: the frame every section page renders inside.

Rendered straight from the Jinja environment with a probe child, so the
shell is pinned independently of any route (routes arrive in #8).
"""

from fastapi.testclient import TestClient

from app.components.web_frontend.rendering import templates
from tests.web.dom import none, one, select, text

PROBE = '<p id="probe">hello</p>'


def render_shell(body: str = PROBE) -> str:
    template = templates.env.from_string(
        '{% extends "layouts/app_shell.html" %}'
        "{% block app_content %}" + body + "{% endblock %}"
    )
    return template.render()


class TestAppShell:
    def test_child_content_lands_in_the_swap_target(self) -> None:
        """``#app-content`` is the one element htmx swaps section fragments
        into, so the child block must render inside it and nowhere else."""
        page = render_shell()
        one(page, "main#app-content #probe")
        one(page, "main")

    def test_sidebar_is_the_navigation(self) -> None:
        page = render_shell()
        one(page, "aside#sidebar nav")
        # The base layout's top bar is replaced, not stacked on top.
        none(page, 'a[href="/dashboard"]')

    def test_the_sidebar_opens_with_the_mark_and_closes_with_its_maker(
        self,
    ) -> None:
        """Top of the column, the app says what it is; foot of it, what
        built it. Both are markup rather than pictures, so both follow
        the theme."""
        page = render_shell()
        mark = one(page, "aside#sidebar a[href='/'] svg")
        assert "text-aegis-teal" in (mark.get("class") or "")
        assert text(one(page, "aside#sidebar a[href='/'] span"))
        assert text(one(page, "[data-powered-by]")) == "Powered by Aegis Stack"

    def test_the_sections_are_grouped_by_what_they_are_about(self) -> None:
        """The heading and the order are one list. A heading written in
        the template would be a second place the nav is decided, and the
        two would disagree the first time a section moved.

        A group CHANGE draws it, so a section carrying no group closes
        the last one with a rule rather than sitting under a heading it
        has nothing to do with.
        """
        from app.components.web_frontend.nav import NAV

        page = render_shell()
        drawn = [text(el) for el in select(page, "#sidebar [data-nav-group]")]
        declared = []
        for entry in NAV:
            if entry.group and entry.group not in declared:
                declared.append(entry.group)
        assert drawn == declared

        # Chat and Settings carry no group, so a rule closes Records
        # rather than leaving them filed under it.
        one(page, "#sidebar nav hr")

        # Railed, a heading is a word with nothing under it.
        for el in select(page, "#sidebar [data-nav-group]") + select(
            page, "#sidebar nav hr"
        ):
            assert "rail-none" in (el.get("class") or "")

    def test_the_sidebar_rails_down_to_its_icons(self) -> None:
        """Collapsing is a stored preference, not page state: theme.js
        puts it on <html> before the first paint, so a railed sidebar
        never starts wide and snaps narrow.

        Every row that collapses says so by wearing the kit's classes,
        and the labels go the sr-only way - an icon-only link still has
        to say which section it is.
        """
        page = render_shell()
        rail = one(page, "aside#sidebar button[data-rail]")
        assert rail.get("aria-controls") == "sidebar"
        assert "setAppearance('sidebar'" in (rail.get("@click") or "")
        # desktop only: on mobile the sidebar is a drawer, not a column
        assert "md:flex" in (rail.get("class") or "")
        assert "hidden" in (rail.get("class") or "")
        labelled = [text(one(el, "span.rail-hide")) for el in select(page, ".rail-row")]
        assert "Overview" in labelled
        assert "Collapse" in labelled

    def test_mobile_toggle_controls_the_sidebar(self) -> None:
        toggle = one(render_shell(), 'button[aria-label="Toggle navigation"]')
        assert toggle.get("aria-controls") == "sidebar"
        assert toggle.get(":aria-expanded") is not None

    def test_toast_region_sits_outside_the_scroll_container(self) -> None:
        """Fixed to the viewport regardless of section scroll, so it must
        not be a descendant of ``#app-content``."""
        page = render_shell()
        one(page, "#toasts")
        none(one(page, "main#app-content"), "#toasts")

    def test_is_not_indexable(self) -> None:
        one(render_shell(), 'meta[name="robots"][content="noindex, nofollow"]')

    def test_carries_no_pulse_user_gate(self) -> None:
        """Steward has no auth; the shell must not wait on a user object
        before painting."""
        page = render_shell()
        for banned in ("appShellGate", "__APP_USER", "/api/v1/auth/me"):
            assert banned not in page, banned
        none(page, "main#app-content script")


class TestTheAssistantButton:
    """The floating button sits over the bottom-right corner, where a
    register's amounts are; the page leaves room under it so the last row
    can scroll clear (#297)."""

    def test_the_page_leaves_room_under_the_button(self) -> None:
        from pathlib import Path

        css = Path("app/components/web_frontend/static/input.css").read_text()
        rule = 'html:not([data-assistant="hide"]) #app-content:not(:has(#chat))'
        assert rule in css
        assert "padding-bottom" in css[css.index(rule) :].split("}")[0]

    def test_the_room_follows_the_button(self, client: TestClient) -> None:
        """No room where there is no button: the chat page (its surface is
        the page, found as app.js finds it) and the preference to hide it."""
        chat = client.get("/chat").text
        assert one(chat, "#app-content #chat") is not None
        none(client.get("/overview").text, "#app-content #chat")
