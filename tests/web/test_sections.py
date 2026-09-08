"""Section routes and the sidebar that links them.

``NAV`` is the one list of sections. Every entry must resolve to a route
that renders both ways, and the sidebar must link every entry the same
way, so a section added to the list is fully wired or loudly missing.
"""

from fastapi.testclient import TestClient
import pytest

from app.components.web_frontend.nav import NAV, Section
from tests.web.dom import none, one, select, text

SECTION_IDS = [section.key for section in NAV]


class TestNav:
    def test_lists_the_finance_sections_in_order(self) -> None:
        assert SECTION_IDS == [
            "overview",
            "accounts",
            "bills",
            "projected",
            "budget",
            "review",
            "settings",
        ]

    def test_keys_and_paths_are_unique(self) -> None:
        assert len({s.key for s in NAV}) == len(NAV)
        assert len({s.path for s in NAV}) == len(NAV)


class TestRoot:
    def test_redirects_to_overview(self, client: TestClient) -> None:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/overview"


@pytest.mark.parametrize("section", NAV, ids=SECTION_IDS)
class TestSectionPage:
    def test_full_load_renders_in_the_shell(
        self, client: TestClient, section: Section
    ) -> None:
        response = client.get(section.path)
        assert response.status_code == 200
        page = response.text
        one(page, "aside#sidebar")
        # A page may name itself more fully than the sidebar word does
        # ("Projected" -> "Projected balance"), never less.
        assert text(one(page, "main#app-content h1")).startswith(section.label)
        assert section.label in text(one(page, "title"))

    def test_htmx_request_renders_the_fragment(
        self, hx: TestClient, section: Section
    ) -> None:
        fragment = hx.get(section.path).text
        none(fragment, "aside")
        none(fragment, "main")
        assert text(one(fragment, "h1")).startswith(section.label)

    def test_sidebar_marks_only_this_section_current(
        self, client: TestClient, section: Section
    ) -> None:
        page = client.get(section.path).text
        current = one(page, 'aside#sidebar nav a[aria-current="page"]')
        assert current.get("href") == section.path


class TestSidebarLinks:
    @pytest.fixture
    def links(self, client: TestClient) -> list:
        return select(client.get("/overview").text, "aside#sidebar nav a")

    def test_one_link_per_section_in_nav_order(self, links: list) -> None:
        assert [a.get("href") for a in links] == [s.path for s in NAV]
        assert [text(a) for a in links] == [s.label for s in NAV]

    def test_every_link_swaps_the_content_area_and_pushes_the_url(
        self, links: list
    ) -> None:
        """A cold load and an htmx swap hit the same URL: no second route
        per view."""
        for a in links:
            assert a.get("hx-get") == a.get("href"), a.get("href")
            assert a.get("hx-target") == "#app-content"
            assert a.get("hx-push-url") == "true"
