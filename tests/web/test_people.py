"""The People tab: the address book everything else points at.

ST-01's UI half. Both render paths, selectors not substrings, per the
``add-page`` skill.
"""

from fastapi.testclient import TestClient

from tests.web.dom import none, one, select, text


class TestThePeoplePage:
    def test_the_page_renders_and_is_marked_current(
        self, client: TestClient
    ) -> None:
        page = client.get("/settings/people").text
        one(page, "#people")
        marked = one(page, '#settings-nav a[aria-current="page"]')
        assert marked.get("href") == "/settings/people"

    def test_nothing_found_says_so(self, client: TestClient) -> None:
        """Asserted through a search that matches nothing rather than an
        empty table: the workers share a database, so whether any party
        exists is another test's business, not this one's."""
        page = client.get("/settings/people", params={"q": "zzzznobody"}).text
        assert "Nobody yet" in text(one(page, "#people"))

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/settings/people").text, "html")

    def test_the_three_the_case_needs(self, client: TestClient) -> None:
        """A person, an agency and a facility - the ticket's own gate,
        through the form a person would actually use."""
        for name, kind in (
            ("James Bedner", "person"),
            ("Dutchess County DSS", "organization"),
            ("Eleanor Nursing Care Center", "organization"),
        ):
            client.post(
                "/settings/people/new",
                data={"name": name, "kind": kind, "sort_name": "", "note": ""},
            )

        page = client.get("/settings/people").text
        rows = [text(cell) for cell in select(page, "#people tbody td")]
        assert "James Bedner" in rows
        assert "Dutchess County DSS" in rows
        # Filed where a reader looks: the person files under B.
        assert "Bedner, James" in rows

    def test_the_guess_is_shown_so_it_can_be_overruled(
        self, client: TestClient
    ) -> None:
        """The app files "James Bedner" under "Bedner, James". A guess
        the reader cannot see is a guess they cannot correct.

        Found by search rather than by being the only row: the workers
        share a database and other tests put people in it.
        """
        client.post(
            "/settings/people/new",
            data={"name": "Quilliam Testcase", "kind": "person", "sort_name": "", "note": ""},
        )
        page = client.get("/settings/people", params={"q": "Quilliam"}).text
        party = one(page, "#people tbody [data-open]")

        form = client.get(party.get("hx-get")).text

        filed = one(form, 'input[name="sort_name"]')
        assert filed.get("value") == "Testcase, Quilliam"

    def test_a_party_with_no_name_is_refused_in_place(
        self, client: TestClient
    ) -> None:
        body = client.post(
            "/settings/people/new",
            data={"name": "   ", "kind": "person", "sort_name": "", "note": ""},
        )
        assert body.status_code == 422
        assert "needs a name" in body.text
        # Nothing was written: a blank-named party would file under a
        # blank sort name, which is the top of the list.
        page = client.get("/settings/people").text
        assert not [
            cell for cell in select(page, "#people tbody td") if not text(cell)
        ]
