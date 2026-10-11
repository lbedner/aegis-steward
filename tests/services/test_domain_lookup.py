"""Going from a name to a domain, and refusing to guess.

The model does know that Delta Dental's site is deltadentalins.com, and
unlike a phone number that guess is cheap to CHECK: fetch it, and accept
it only if the organization's name is actually on the page. Verification
is the whole design. A guess that cannot be confirmed is dropped, not
filed with a hedge.

Everything here runs against a local fixture server. A test that reaches
the internet is a test that fails on a train, and one that passes
because a real site happened to be up has proved nothing about the code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from typing import Any

import pytest

from tests._fake_dns import answering, resolving_to

PAGES: dict[str, tuple[int, str]] = {
    # The confirming site: the name is in the title.
    "/": (200, "<html><title>Delta Dental of New York</title><body>Plans</body>"),
    "/contact": (
        200,
        "<html><body>Delta Dental of New York<br>"
        "P.O. Box 660138<br>Dallas, TX 75266-0138<br>"
        "Call 1-888-282-8784<br>mail us at help@example.com</body></html>",
    ),
    # A parked domain: answers, says nothing about anybody.
    "/parked": (200, "<html><title>Buy this domain</title><body>For sale</body>"),
    "/missing": (404, "not found"),
    # Redirects somewhere else entirely.
    "/away": (301, "http://elsewhere.invalid/"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
        status, body = PAGES.get(self.path, (404, "no"))
        if status == 301:
            self.send_response(301)
            self.send_header("Location", body)
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Quiet. A test server narrating every request buries the run."""


@pytest.fixture(autouse=True)
def fixture_server_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture server listens on loopback, which the guarded fetch
    refuses (#485). Only that one address is let through here; the rest
    of the check still runs."""
    from app.core import safe_fetch

    real = safe_fetch.is_public
    monkeypatch.setattr(
        safe_fetch, "is_public", lambda address: address == "127.0.0.1" or real(address)
    )


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


class TestConfirmingADomain:
    @pytest.mark.asyncio
    async def test_a_site_carrying_the_name_is_confirmed(self, site: str) -> None:
        from app.services.matters.domain_lookup import confirm

        found = await confirm("Delta Dental of New York, Inc.", [site], scheme="http")
        assert found == site

    @pytest.mark.asyncio
    async def test_a_parked_domain_is_refused(self, site: str) -> None:
        """It answers 200 and says nothing about anybody. A guess that
        cannot be confirmed is dropped, not filed with a hedge."""
        from app.services.matters.domain_lookup import confirm

        assert await confirm("Delta Dental", [f"{site}/parked"], scheme="http") is None

    @pytest.mark.asyncio
    async def test_a_404_is_refused(self, site: str) -> None:
        from app.services.matters.domain_lookup import confirm

        assert await confirm("Delta Dental", [f"{site}/missing"], scheme="http") is None

    @pytest.mark.asyncio
    async def test_the_first_candidate_that_confirms_wins(self, site: str) -> None:
        from app.services.matters.domain_lookup import confirm

        found = await confirm(
            "Delta Dental of New York",
            [f"{site}/missing", f"{site}/parked", site],
            scheme="http",
        )
        assert found == site

    @pytest.mark.asyncio
    async def test_nothing_reachable_confirms_nothing(self) -> None:
        """No network, a dead host, a typo: all the same answer, and none
        of them an exception a caller has to catch."""
        from app.services.matters.domain_lookup import confirm

        assert await confirm("Anybody", ["127.0.0.1:1"], scheme="http") is None

    @pytest.mark.asyncio
    async def test_the_budget_bounds_how_many_are_tried(self, site: str) -> None:
        """A list of forty guesses is forty requests to somebody else's
        servers. The budget is the whole defence against a lookup that
        turns into a scan."""
        from app.services.matters.domain_lookup import CANDIDATE_BUDGET, confirm

        tried = [f"{site}/missing"] * (CANDIDATE_BUDGET + 5) + [site]
        assert await confirm("Delta Dental", tried, scheme="http") is None


class TestCorroboratingAgainstWhatIsAlreadyKnown:
    """The name on a page proves the page MENTIONS the organization. It
    does not prove the page belongs to it.

    There is more than one "Eleanor Nursing Care Center" in the country,
    and a directory listing carries the name more prominently than the
    home page does. Either would confirm on the name alone, and then
    somebody else's phone number is filed under your grandfather's
    nursing home - with a citation, which is what makes approving it
    feel safe.

    So when the record already holds a postcode or a number, the page
    has to carry it too.
    """

    @pytest.mark.asyncio
    async def test_a_page_missing_a_known_postcode_is_refused(self, site: str) -> None:
        from app.services.matters.domain_lookup import confirm

        # The site says "Delta Dental of New York" but nothing in Hyde
        # Park. Known-good detail we already hold: a different postcode.
        found = await confirm(
            "Delta Dental of New York",
            [site],
            scheme="http",
            corroborate={"12538"},
        )
        assert found is None

    @pytest.mark.asyncio
    async def test_a_page_carrying_a_known_detail_is_confirmed(self, site: str) -> None:
        from app.services.matters.domain_lookup import confirm

        found = await confirm(
            "Delta Dental of New York",
            [f"{site}/contact"],
            scheme="http",
            corroborate={"75266-0138"},
        )
        assert found == f"{site}/contact"

    @pytest.mark.asyncio
    async def test_a_number_written_differently_still_corroborates(
        self, site: str
    ) -> None:
        """The record holds "1-888-282-8784" and the page may print
        "(888) 282-8784". One number, two spellings."""
        from app.services.matters.domain_lookup import confirm

        found = await confirm(
            "Delta Dental of New York",
            [f"{site}/contact"],
            scheme="http",
            corroborate={"(888) 282-8784"},
        )
        assert found == f"{site}/contact"

    @pytest.mark.asyncio
    async def test_with_nothing_known_the_name_still_decides(self, site: str) -> None:
        """A contact with an empty record has nothing to corroborate
        against. That is the weaker check, and it is the one the card
        has to be read carefully for."""
        from app.services.matters.domain_lookup import confirm

        assert await confirm("Delta Dental of New York", [site], scheme="http")


class TestTheNameOnThePage:
    def test_a_suffix_is_not_part_of_the_name(self) -> None:
        from app.services.matters.domain_lookup import says

        page = "<title>Delta Dental of New York</title>"
        assert says(page, "Delta Dental of New York, Inc.")
        assert says(page, "The Delta Dental of New York LLC")

    def test_a_different_organization_is_not_a_match(self) -> None:
        from app.services.matters.domain_lookup import says

        assert not says("<title>Guardian Life</title>", "Delta Dental")

    def test_case_and_spacing_do_not_decide_it(self) -> None:
        from app.services.matters.domain_lookup import says

        assert says("<title>DELTA   DENTAL</title>", "Delta Dental")


class TestReadingTheContactPage:
    @pytest.mark.asyncio
    async def test_it_reads_the_reach_fields_with_the_url(self, site: str) -> None:
        from app.services.matters.domain_lookup import contact_page

        found = await contact_page(site, scheme="http", paths=("/contact",))
        fields = {offer["field"]: offer for offer in found}
        assert fields["phone"]["value"] == "1-888-282-8784"
        assert "Dallas, TX 75266-0138" in fields["address"]["value"]
        # Every row cites the URL it was read from, or it is not a row.
        assert fields["phone"]["url"].endswith("/contact")

    @pytest.mark.asyncio
    async def test_a_site_with_no_contact_page_offers_nothing(self, site: str) -> None:
        from app.services.matters.domain_lookup import contact_page

        assert await contact_page(site, scheme="http", paths=("/nope",)) == []


class TestReadingOnlyThePublicInternet:
    """The assistant chooses the host, and an injected document can choose
    it for her (#485). A name resolving inward is never fetched, and the
    one redirect is resolved and checked again on its own."""

    @pytest.mark.asyncio
    async def test_a_host_resolving_to_the_metadata_service_is_not_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        from app.services.matters.domain_lookup import confirm

        resolving_to(monkeypatch, ["169.254.169.254"])
        sent = answering(monkeypatch, httpx.Response(200, text="Anybody"))

        assert await confirm("Anybody", ["metadata.example"]) is None
        assert sent == []

    @pytest.mark.asyncio
    async def test_the_redirect_is_resolved_and_checked_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same host, second lookup: a name that rebinds inward between the
        first fetch and its redirect is refused at the redirect."""
        import httpx

        from app.services.matters.domain_lookup import _read

        asked = resolving_to(monkeypatch, ["93.184.216.34"], ["10.0.0.5"])
        sent = answering(
            monkeypatch, httpx.Response(301, headers={"location": "/contact"})
        )

        assert await _read("https://rebind.example/", "rebind.example") is None
        assert [str(request.url) for request in sent] == ["https://93.184.216.34/"]
        assert asked == ["rebind.example", "rebind.example"]

    @staticmethod
    def _counted(pulled: list[int], chunks: int) -> AsyncIterator[bytes]:
        """A 64 KiB-a-chunk body that records how much of it was pulled."""

        async def body() -> AsyncIterator[bytes]:
            for _ in range(chunks):
                pulled.append(1)
                yield b"x" * 65536

        return body()

    @pytest.mark.asyncio
    async def test_an_endless_page_is_read_only_to_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        from app.core.safe_fetch import MAX_BODY_BYTES
        from app.services.matters.domain_lookup import _read

        pulled: list[int] = []
        resolving_to(monkeypatch, ["93.184.216.34"])
        answering(
            monkeypatch,
            httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=self._counted(pulled, 200),
            ),
        )

        page = await _read("https://huge.example/", "huge.example")

        assert page is not None and len(page) == MAX_BODY_BYTES
        assert len(pulled) <= MAX_BODY_BYTES // 65536 + 1

    @pytest.mark.asyncio
    async def test_a_page_that_is_not_text_is_not_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        from app.services.matters.domain_lookup import _read

        pulled: list[int] = []
        resolving_to(monkeypatch, ["93.184.216.34"])
        answering(
            monkeypatch,
            httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=self._counted(pulled, 8),
            ),
        )

        assert await _read("https://pdf.example/", "pdf.example") is None
        assert pulled == []
