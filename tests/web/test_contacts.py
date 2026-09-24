"""The People tab: the address book everything else points at.

ST-01's UI half. Both render paths, selectors not substrings, per the
``add-page`` skill.
"""

from fastapi.testclient import TestClient

from app.services.matters.words import WORDS
from tests.web.dom import card, none, one, select, text


class TestThePeoplePage:
    def test_the_page_renders_and_is_marked_current(self, client: TestClient) -> None:
        page = client.get("/contacts").text
        one(page, "#contacts")
        # A Records section of its own, beside Matters - not a Settings tab.
        marked = one(page, 'a[aria-current="page"]')
        assert marked.get("href") == "/contacts"
        none(page, "#settings-nav")

    def test_nothing_found_says_so(self, client: TestClient) -> None:
        """Asserted through a search that matches nothing rather than an
        empty table: the workers share a database, so whether any party
        exists is another test's business, not this one's."""
        page = client.get("/contacts", params={"q": "zzzznobody"}).text
        assert "Nobody yet" in text(one(page, "#contacts"))

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/contacts").text, "html")

    def test_the_three_the_case_needs(self, client: TestClient) -> None:
        """A person, an agency and a facility - the ticket's own gate,
        through the form a person would actually use."""
        for name, kind in (
            ("James Bedner", "person"),
            ("Dutchess County DSS", "organization"),
            ("Eleanor Nursing Care Center", "organization"),
        ):
            client.post(
                "/contacts/new",
                data={"name": name, "kind": kind, "sort_name": "", "note": ""},
            )

        page = client.get("/contacts").text
        rows = [text(cell) for cell in select(page, "#contacts tbody td")]
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
            "/contacts/new",
            data={
                "name": "Quilliam Testcase",
                "kind": "person",
                "sort_name": "",
                "note": "",
            },
        )
        page = client.get("/contacts", params={"q": "Quilliam"}).text
        # The row opens the contact's page; the form is reached from it.
        row = one(page, "#contacts tbody [data-open]")
        form = client.get(row.get("href") + "/edit").text

        filed = one(form, 'input[name="sort_name"]')
        assert filed.get("value") == "Testcase, Quilliam"

    def test_a_party_with_no_name_is_refused_in_place(self, client: TestClient) -> None:
        body = client.post(
            "/contacts/new",
            data={"name": "   ", "kind": "person", "sort_name": "", "note": ""},
        )
        assert body.status_code == 422
        assert "needs a name" in body.text
        # Nothing was written: a blank-named party would file under a
        # blank sort name, which is the top of the list.
        page = client.get("/contacts").text
        assert not [
            cell for cell in select(page, "#contacts tbody td") if not text(cell)
        ]


def _contact(client: TestClient, name: str, kind: str) -> int:
    """A party by the front door, and its id off the list."""
    client.post(
        "/contacts/new", data={"name": name, "kind": kind, "sort_name": "", "note": ""}
    )
    rows = select(
        client.get("/contacts", params={"q": name}).text, "#contacts tbody tr"
    )
    # The newest row of that name: the app-owned database is shared for
    # the run, and another file may have filed the same party before.
    return max(int(r.get("id").split("-")[-1]) for r in rows if name in text(r))


class TestAContactHasAPage:
    """A contact is worked - logged into, written to, read off - so it
    is a page, not a dialog, like a matter. The kind decides the order."""

    def test_an_organization_is_a_place(self, client: TestClient) -> None:
        party_id = _contact(
            client, "New York State and Local Retirement System", "organization"
        )
        page = client.get(f"/contacts/{party_id}").text
        section = one(page, "#contact")
        assert section.get("data-kind") == "organization"
        assert one(page, 'a[aria-current="page"]').get("href") == "/contacts"
        # A place: how to reach it, its sign-ins and what it says are shown
        # even while empty; its accounts are the ones held there.
        one(page, "[data-back]")
        card(page, WORDS["reach"])
        one(page, f'[hx-get="/contacts/{party_id}/signins"]')
        one(page, "[data-says]")
        none(page, "[data-about]")
        one(page, "#contact-paper")

    def test_a_person_is_a_subject(self, client: TestClient) -> None:
        party_id = _contact(client, "Quentin Testcase", "person")
        page = client.get(f"/contacts/{party_id}").text
        assert one(page, "#contact").get("data-kind") == "person"
        one(page, "[data-about]")
        none(page, "[data-says]")
        none(page, f'[hx-get="/contacts/{party_id}/signins"]')

    def test_fragment_has_no_shell(self, hx: TestClient, client: TestClient) -> None:
        party_id = _contact(client, "Fragment Testcase", "person")
        none(hx.get(f"/contacts/{party_id}").text, "html")

    def test_the_edit_dialog_is_reached_from_the_page(self, client: TestClient) -> None:
        party_id = _contact(client, "Edith Testcase", "person")
        page = client.get(f"/contacts/{party_id}").text
        opener = one(page, f'button[hx-get="/contacts/{party_id}/edit"]')
        assert opener is not None
        form = client.get(f"/contacts/{party_id}/edit").text
        assert one(form, 'input[name="name"]').get("value") == "Edith Testcase"

    def test_their_paper_is_filed_with_them(self, client: TestClient) -> None:
        """A statement is the fund's, whatever matter later needs it: the
        shelf on the contact, its own tag, and the same document dialog."""
        from tests._pdf import pdf_bytes

        party_id = _contact(client, "NYSLRS Paper Testcase", "organization")
        page = client.get(f"/contacts/{party_id}").text
        one(page, f'button[hx-get="/contacts/{party_id}/documents/new"]')
        # The shelf says what it is for; an empty card with a name is a riddle.
        assert WORDS["their_paper_is"] in text(one(page, "#contact-paper"))

        answer = client.post(
            f"/contacts/{party_id}/documents/new",
            files={
                "file": (
                    "statement.pdf",
                    pdf_bytes(["Gross benefit $1,200.00"]),
                    "application/pdf",
                )
            },
        )
        assert answer.status_code == 200

        page = client.get(f"/contacts/{party_id}").text
        row = one(page, "#contact-paper tbody tr")
        assert "statement.pdf" in text(row)
        opener = one(row, "[data-open]")
        dialog = client.get(opener.get("hx-get")).text
        one(dialog, "[data-original]")


class TestANameIsADoor:
    """Wherever a contact is named, the name opens their page - one macro,
    so no card draws it as text and no other as a dialog."""

    def test_on_a_place_the_people_who_sign_in(self, client: TestClient) -> None:
        place = _contact(client, "Door Retirement System", "organization")
        person = _contact(client, "Door Testcase", "person")
        client.post(
            f"/contacts/{person}/signins/new",
            data={
                "label": "Retirement portal",
                "site_party_id": str(place),
                "username": "d",
                "secret": "",
            },
        )
        block = client.get(f"/contacts/{place}/signins").text
        assert (
            one(block, f'a[data-contact="{person}"]').get("href")
            == f"/contacts/{person}"
        )


class TestLookingItUpByHand:
    """The assistant's lookup, on a button: one card in Review, never a
    write by itself (#173 follow-up, 2026-09-23)."""

    @staticmethod
    def _made(client: TestClient, name: str, kind: str) -> int:
        client.post(
            "/contacts/new",
            data={"name": name, "kind": kind, "sort_name": "", "note": ""},
        )
        page = client.get("/contacts", params={"q": name}).text
        link = next(a for a in select(page, "#contacts tbody a") if text(a) == name)
        return int(link.get("href").rsplit("/", 1)[-1])

    def test_an_organization_offers_it_and_it_files_a_card(
        self, client: TestClient, monkeypatch
    ) -> None:
        import app.services.matters.domain_lookup as domain_lookup

        async def web_offers(name: str, guesses: list, known: set) -> dict:
            return {
                "confirmed": "example.org/x/",
                "found_by": "search",
                "offers": [
                    {
                        "field": "phone",
                        "value": "(845) 555-0142",
                        "url": "https://example.org/x/",
                        "source": "found by web search; read at https://example.org/x/",
                    }
                ],
            }

        monkeypatch.setattr(domain_lookup, "web_offers", web_offers)
        party_id = self._made(client, "Testcase Lookup Org", "organization")
        page = client.get(f"/contacts/{party_id}").text
        button = one(page, f'[hx-post="/contacts/{party_id}/look-up"]')
        assert text(button) == WORDS["look_up"]

        answer = client.post(f"/contacts/{party_id}/look-up")

        # The card comes to the reader: the dialog opens on it, drawn by
        # the one card route the chat uses, not a second rendering.
        assert answer.status_code == 200
        loader = one(answer.text, "[data-component-load='pending_change']")
        assert loader.get("hx-get").startswith("/chat/components/change/")
        assert button.get("hx-target") == "#dialog-body"

    def test_the_page_redraws_when_its_card_is_decided(
        self, client: TestClient
    ) -> None:
        """Approve the phone number in the dialog, and it appears on the
        page behind it without a reload."""
        party_id = self._made(client, "Testcase Lookup Redraw", "organization")
        page = client.get(f"/contacts/{party_id}").text
        redraws = one(page, "#contact [data-redraws]")
        assert "change:resolved" in redraws.get("hx-trigger")
        assert redraws.get("hx-select") == "#contact"
        # Childless, and never on the section: htmx hands hx-select and
        # hx-target down to every block inside, and the lazy sign-ins
        # swapped the whole contact away (2026-09-24).
        assert len(redraws) == 0
        section = one(page, "#contact")
        assert section.get("hx-select") is None and section.get("hx-target") is None

    def test_nothing_new_says_so(self, client: TestClient, monkeypatch) -> None:
        import app.services.matters.domain_lookup as domain_lookup
        from tests.web.dom import triggers

        async def web_offers(name: str, guesses: list, known: set) -> dict:
            return {"confirmed": None, "found_by": None, "offers": []}

        monkeypatch.setattr(domain_lookup, "web_offers", web_offers)
        party_id = self._made(client, "Testcase Lookup Empty", "organization")

        answer = client.post(f"/contacts/{party_id}/look-up")

        # Nothing to decide, so no dialog: a 204 swaps nothing.
        assert answer.status_code == 204
        assert "Nothing new" in triggers(answer)["toast"]["text"]


def test_a_rows_note_is_drawn_under_it(add_template) -> None:
    """The one macro every card draws with puts a row's note on a line
    of its own."""
    from app.components.web_frontend.rendering import templates

    add_template(
        "probe_note.html",
        '{% from "components/macros/changes.html" import display %}{{ display(change) }}',
    )
    html = templates.get_template("probe_note.html").render(
        change={
            "display": [
                {"label": "Contact", "value": "Eleanor"},
                {"label": "Phone", "value": "- → 555", "note": "read at https://x"},
            ]
        }
    )
    note = one(html, "li [data-note]")
    assert text(note) == "read at https://x"
