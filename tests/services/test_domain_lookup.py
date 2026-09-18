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

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from typing import Any

import pytest

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

        found = await confirm(
            "Delta Dental of New York, Inc.", [site], scheme="http"
        )
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
    async def test_a_site_with_no_contact_page_offers_nothing(
        self, site: str
    ) -> None:
        from app.services.matters.domain_lookup import contact_page

        assert await contact_page(site, scheme="http", paths=("/nope",)) == []
