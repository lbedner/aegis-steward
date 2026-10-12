"""Web search, for the organization nobody has heard of (#173).

Off without a key, cached hard, and budgeted per day: the cache and the
budget bound how much of a household's pattern ever leaves, which is
the privacy mechanism, not only a cost control. A result is a candidate
for ``domain_lookup.confirm``, never a fact.
"""

from typing import Any

import pytest

from app.core import search
from app.core.cache import CacheService


@pytest.fixture
def brave(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A fake Brave: records each query it is asked, answers with one hit."""
    asked: list[str] = []

    async def fetch(query: str, *, key: str, limit: int) -> list[search.SearchHit]:
        asked.append(query)
        return [
            search.SearchHit(
                title="Maple Nursing Care Center",
                url="https://www.maplenursingcare.org/about",
                snippet="Skilled nursing in Lakeside, NY",
            )
        ]

    monkeypatch.setattr(search, "_brave", fetch)
    monkeypatch.setattr(search, "get_cache", lambda: CacheService())
    monkeypatch.setattr(search.settings, "BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(search, "DAILY_BUDGET", 3)
    return asked


class TestSearch:
    @pytest.mark.asyncio
    async def test_no_key_means_off_and_asks_nobody(
        self, brave: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(search.settings, "BRAVE_SEARCH_API_KEY", None)
        assert await search.search("Maple Nursing Care Center") == []
        assert brave == []

    @pytest.mark.asyncio
    async def test_a_hit_comes_back_as_title_url_snippet(
        self, brave: list[str]
    ) -> None:
        results = await search.search("Maple Nursing Care Center")
        assert results[0].url == "https://www.maplenursingcare.org/about"
        assert brave == ["Maple Nursing Care Center"]

    @pytest.mark.asyncio
    async def test_a_repeated_lookup_emits_no_new_query(
        self, brave: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = CacheService()
        monkeypatch.setattr(search, "get_cache", lambda: cache)
        await search.search("Maple Nursing Care Center")
        await search.search("  maple nursing care center ")
        assert len(brave) == 1

    @pytest.mark.asyncio
    async def test_the_daily_budget_is_a_hard_stop(
        self, brave: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = CacheService()
        monkeypatch.setattr(search, "get_cache", lambda: cache)
        for name in ("One", "Two", "Three", "Four", "Five"):
            await search.search(name)
        assert brave == ["One", "Two", "Three"]

    def test_a_candidate_is_the_page_that_matched_not_just_its_host(self) -> None:
        """Live: Maple's page is examplehealthcare.com/maple/, the
        parent company's page for the facility. Cut to the host, confirm()
        checked the parent's home page, which is not theirs (2026-09-23)."""
        hits = [
            search.SearchHit(url="https://examplehealthcare.com/maple/"),
            search.SearchHit(url="https://www.examplehealthcare.com/maple/?utm=x"),
            search.SearchHit(url="http://themaple.net/services.asp"),
        ]
        assert search.candidates(hits) == [
            "examplehealthcare.com/maple/",
            "themaple.net/services.asp",
        ]

    def test_a_directory_is_never_a_candidate(self) -> None:
        """A Yelp page, or the state's facility profile, carries the name
        and address more prominently than the organization's own site, and
        confirm() would accept it as theirs. Directories are dropped first."""
        hits = [
            search.SearchHit(url="https://www.yelp.com/biz/maple-nursing"),
            search.SearchHit(url="https://www.facebook.com/maplenursing"),
            search.SearchHit(
                url="https://health.usnews.com/best-nursing-homes/area/ny/maple"
            ),
            search.SearchHit(
                url="https://profiles.health.ny.gov/nursing_home/view/150357"
            ),
            search.SearchHit(url="https://maplenursingcare.org/"),
        ]
        assert search.candidates(hits) == ["maplenursingcare.org/"]


class TestLookUpContactFallsBackToSearch:
    @pytest.mark.asyncio
    async def test_when_the_guesses_fail_a_search_is_verified_the_same_way(
        self, brave: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Live: Maple Nursing Care Center, three guessed domains, none
        confirmed, record left blank (2026-09-18)."""
        from app.services.matters import ai_tools, domain_lookup

        tried: list[list[str]] = []

        async def confirm(name: str, candidates: list[str], **_: Any) -> str | None:
            tried.append(list(candidates))
            page = "maplenursingcare.org/about"
            return page if page in candidates else None

        async def contact_page(domain: str, **_: Any) -> list[dict[str, Any]]:
            return [
                {
                    "field": "phone",
                    "value": "555-555-0100",
                    "url": f"https://{domain}/contact",
                }
            ]

        monkeypatch.setattr(domain_lookup, "confirm", confirm)
        monkeypatch.setattr(domain_lookup, "contact_page", contact_page)

        found = await ai_tools.look_up_contact(
            "Maple Nursing Care Center", ["maplenursingcarecenter.com"]
        )

        assert tried[0] == ["maplenursingcarecenter.com"]
        assert "maplenursingcare.org/about" in tried[1]
        assert found["confirmed"] == "maplenursingcare.org/about"
        assert found["found_by"] == "search"
        assert found["offers"][0]["value"] == "555-555-0100"
        # The card can never mistake it for their own paper.
        assert found["offers"][0]["source"].startswith("found by web search; read at ")

    @pytest.mark.asyncio
    async def test_a_confirmed_guess_never_searches(
        self, brave: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters import ai_tools, domain_lookup

        async def confirm(name: str, candidates: list[str], **_: Any) -> str | None:
            return candidates[0]

        async def contact_page(domain: str, **_: Any) -> list[dict[str, Any]]:
            return []

        monkeypatch.setattr(domain_lookup, "confirm", confirm)
        monkeypatch.setattr(domain_lookup, "contact_page", contact_page)

        found = await ai_tools.look_up_contact("Delta Dental", ["deltadentalins.com"])

        assert found["found_by"] == "guess"
        assert brave == []


class TestTheFoundPageIsReadFirst:
    @pytest.mark.asyncio
    async def test_a_page_with_a_path_is_read_before_the_hosts_contact_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A parent company's /contact is not the facility's."""
        from app.services.matters import domain_lookup

        read: list[str] = []

        async def fake_read(url: str, host: str) -> str | None:
            read.append(url)
            return "<p>Call us: (555) 555-0100</p>" if url.endswith("/maple/") else None

        monkeypatch.setattr(domain_lookup, "_read", fake_read)
        rows = await domain_lookup.contact_page("examplehealthcare.com/maple/")

        assert read[0] == "https://examplehealthcare.com/maple/"
        assert rows and rows[0]["url"] == "https://examplehealthcare.com/maple/"


@pytest.mark.asyncio
async def test_the_app_says_who_it_is_when_it_reads_a_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live: examplehealthcare.com answers 403 to the bare python-httpx
    agent and 200 to one that names the app (2026-09-23). Honest, never
    a browser disguise."""
    import httpx

    from app.services.matters import domain_lookup
    from tests._fake_dns import answering, resolving_to

    resolving_to(monkeypatch, ["93.184.216.34"])
    sent = answering(monkeypatch, httpx.Response(200, text="Maple"))
    await domain_lookup._read("https://example.org/", "example.org")

    assert sent[0].headers["user-agent"] == domain_lookup.USER_AGENT
    assert "AegisSteward" in domain_lookup.USER_AGENT


class TestBravesAnswerIsParsed:
    def test_a_result_becomes_a_search_hit(self) -> None:
        body = {
            "web": {
                "results": [
                    {
                        "title": "The Maple",
                        "url": "https://examplehealthcare.com/maple/",
                        "description": "Skilled nursing",
                        "profile": {"name": "ignored"},
                    }
                ]
            },
            "query": {"original": "Maple"},
        }
        hits = search.parse_brave(body)
        assert hits == [
            search.SearchHit(
                title="The Maple",
                url="https://examplehealthcare.com/maple/",
                snippet="Skilled nursing",
            )
        ]

    def test_an_answer_with_no_web_results_is_no_hits(self) -> None:
        assert search.parse_brave({"type": "search"}) == []
        assert search.parse_brave({"web": {"results": [{"title": "no url"}]}}) == []
