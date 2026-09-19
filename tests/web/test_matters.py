"""The Matters section: cases, and who is in them.

ST-03's UI half. Both render paths, selectors not substrings.
"""

from fastapi.testclient import TestClient

from tests.web.dom import none, one, select, text


def _party(client: TestClient, name: str, kind: str) -> None:
    client.post(
        "/contacts/new",
        data={"name": name, "kind": kind, "sort_name": "", "note": ""},
    )


class TestTheMattersPage:
    def test_empty_says_so(self, client: TestClient) -> None:
        page = client.get("/matters").text
        one(page, "#matters")
        assert "No matters yet" in text(one(page, "#matters"))

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/matters").text, "html")

    def test_opening_a_case_names_who_it_is_about_and_with(
        self, client: TestClient
    ) -> None:
        """The columns say who a case is ABOUT and who it is WITH; the
        participant list is what the page reads. Both have to be true,
        so opening a matter writes the links too."""
        _party(client, "James Bedner", "person")
        _party(client, "Dutchess County DSS", "organization")
        people = client.get("/contacts").text
        ids = [
            el.get("hx-get").rsplit("/", 1)[-1]
            for el in select(people, "#contacts tbody [data-open]")
        ]

        client.post(
            "/matters/new",
            data={
                "title": "Medicaid renewal",
                "kind": "medicaid",
                "reference": "MA258760XX",
                "subject_party_id": ids[0],
                "counterpart_party_id": ids[1],
                "opened_on": "2026-08-20",
            },
        )

        listing = client.get("/matters").text
        assert "MA258760XX" in text(one(listing, "#matters"))
        opened = one(listing, "#matters tbody [data-open]")

        page = client.get(opened.get("hx-get")).text
        roles = {text(el) for el in select(page, "#matter [data-role]")}
        assert roles == {"Subject", "Agency"}

    def test_a_second_letter_does_not_open_a_second_case(
        self, client: TestClient
    ) -> None:
        """The agency's own number is how two letters agree they are
        about the same thing. A duplicate case is how the second letter
        gets lost."""
        for _ in range(2):
            client.post(
                "/matters/new",
                data={
                    "title": "Medicaid renewal",
                    "kind": "medicaid",
                    "reference": "MA258760XX",
                    "subject_party_id": "",
                    "counterpart_party_id": "",
                    "opened_on": "",
                },
            )

        rows = select(client.get("/matters").text, "#matters tbody tr")
        assert len(rows) == 1

    def test_a_matter_needs_a_title(self, client: TestClient) -> None:
        before = len(select(client.get("/matters").text, "#matters tbody tr"))

        answer = client.post(
            "/matters/new",
            data={
                "title": "  ",
                "kind": "",
                "reference": "MA-NO-TITLE",
                "subject_party_id": "",
                "counterpart_party_id": "",
                "opened_on": "",
            },
        )

        assert answer.status_code == 422
        assert "needs a title" in answer.text
        # Counted, not "the page is empty": tests share a database, and
        # a neighbour's matter is not this one's failure.
        listing = client.get("/matters").text
        assert len(select(listing, "#matters tbody tr")) == before
        assert "MA-NO-TITLE" not in listing


def _matter(client: TestClient, reference: str) -> str:
    """Open a case and hand back the path of its page."""
    client.post(
        "/matters/new",
        data={
            "title": "Medicaid renewal",
            "kind": "medicaid",
            "reference": reference,
            "subject_party_id": "",
            "counterpart_party_id": "",
            "opened_on": "2026-08-20",
        },
    )
    listing = client.get(f"/matters?q={reference}").text
    rows = [
        el.get("hx-get")
        for el in select(listing, "#matters tbody [data-open]")
        if reference in text(el.getparent().getparent())
    ]
    return rows[-1]


ASKED = "\n".join(
    (
        "1. A copy of the power of attorney",
        "2. Proof of GROSS monthly income for each pension",
        "3. Resource values as of 1 August 2026",
    )
)


class TestWhatWasAskedFor:
    """ST-05's gate: one request, three items, each markable on its own,
    and the request satisfied only when every item is."""

    def test_a_letter_records_as_one_request_with_its_items(
        self, client: TestClient
    ) -> None:
        page = _matter(client, "MA-REQ-1")
        client.post(
            page + "/requests/new",
            data={
                "asked": ASKED,
                "due_on": "2026-09-08",
                "received_on": "2026-08-24",
                "requester_party_id": "",
            },
        )

        drawn = client.get(page).text
        block = one(drawn, "#matter-requests")
        assert len(select(block, "[data-request]")) == 1
        asked = [text(el) for el in select(block, "[data-asked]")]
        assert asked == [
            "A copy of the power of attorney",
            "Proof of GROSS monthly income for each pension",
            "Resource values as of 1 August 2026",
        ]
        assert text(one(block, "[data-standing]")) == "0 of 3"

    def test_marking_one_item_leaves_the_others_standing(
        self, client: TestClient
    ) -> None:
        page = _matter(client, "MA-REQ-2")
        client.post(
            page + "/requests/new",
            data={"asked": ASKED, "due_on": "2026-09-08", "received_on": ""},
        )
        items = select(client.get(page).text, "#matter-requests [data-item]")

        answer = client.post(
            f"/matters/requests/items/{items[0].get('data-item')}/mark/satisfied"
        )

        assert answer.status_code == 200
        none(answer.text, "html")
        card = one(answer.text, "[data-request]")
        assert text(one(card, "[data-standing]")) == "1 of 3"
        assert text(one(card, "[data-tone]")) == "open"

    def test_the_request_reads_satisfied_only_when_every_item_is(
        self, client: TestClient
    ) -> None:
        page = _matter(client, "MA-REQ-3")
        client.post(
            page + "/requests/new",
            data={"asked": ASKED, "due_on": "2026-09-08", "received_on": ""},
        )
        ids = [
            el.get("data-item")
            for el in select(client.get(page).text, "#matter-requests [data-item]")
        ]

        for item_id in ids:
            last = client.post(f"/matters/requests/items/{item_id}/mark/satisfied")

        card = one(last.text, "[data-request]")
        assert text(one(card, "[data-standing]")) == "3 of 3"
        assert text(one(card, "[data-tone]")) == "satisfied"

    def test_an_item_can_be_put_back(self, client: TestClient) -> None:
        page = _matter(client, "MA-REQ-4")
        client.post(page + "/requests/new", data={"asked": "A copy of the POA"})
        item = one(client.get(page).text, "#matter-requests [data-item]")

        client.post(f"/matters/requests/items/{item.get('data-item')}/mark/waived")
        back = client.post(
            f"/matters/requests/items/{item.get('data-item')}/mark/needed"
        )

        assert text(one(back.text, "[data-standing]")) == "0 of 1"

    def test_a_request_needs_something_asked(self, client: TestClient) -> None:
        page = _matter(client, "MA-REQ-5")
        answer = client.post(page + "/requests/new", data={"asked": "   \n\n"})

        assert answer.status_code == 422
        assert "at least one thing" in answer.text
        assert select(client.get(page).text, "#matter-requests [data-request]") == []

    def test_an_unknown_status_is_refused(self, client: TestClient) -> None:
        page = _matter(client, "MA-REQ-6")
        client.post(page + "/requests/new", data={"asked": "A copy of the POA"})
        item = one(client.get(page).text, "#matter-requests [data-item]")

        answer = client.post(
            f"/matters/requests/items/{item.get('data-item')}/mark/lost"
        )

        assert answer.status_code == 400


class TestThePaperThatAnswers:
    """An item asks for the power of attorney; the answer is the
    document, one click from the line that asked for it."""

    def _item(self, client: TestClient, reference: str) -> tuple[str, str]:
        page = _matter(client, reference)
        client.post(
            page + "/requests/new",
            data={"asked": "A copy of the power of attorney", "due_on": "2026-09-08"},
        )
        item = one(client.get(page).text, "#matter-requests [data-item]")
        return page, str(item.get("data-item"))

    def test_adding_one_files_it_and_answers_the_item(self, client: TestClient) -> None:
        from tests._pdf import pdf_bytes

        page, item_id = self._item(client, "MA-DOC-1")
        base = f"/matters/requests/items/{item_id}"

        answer = client.post(
            base + "/attach",
            files={
                "file": ("poa.pdf", pdf_bytes(["Power of attorney"]), "application/pdf")
            },
        )
        assert answer.status_code == 200

        drawn = client.get(page).text
        door = one(drawn, "#matter-requests [data-document]")
        assert "poa.pdf" in text(door)
        # Attaching IS the answer: nobody files the document and then
        # says separately that the item is satisfied.
        assert text(one(drawn, "#matter-requests [data-standing]")) == "1 of 1"

    def test_the_document_opens_from_the_line_that_asked(
        self, client: TestClient
    ) -> None:
        from tests._pdf import pdf_bytes

        page, item_id = self._item(client, "MA-DOC-2")
        client.post(
            f"/matters/requests/items/{item_id}/attach",
            files={
                "file": ("poa.pdf", pdf_bytes(["Power of attorney"]), "application/pdf")
            },
        )
        door = one(client.get(page).text, "#matter-requests [data-document]")

        viewer = client.get(door.get("hx-get")).text

        none(viewer, "html")
        assert one(viewer, "[data-original]") is not None

    def test_a_document_on_another_matter_is_not_reachable(
        self, client: TestClient
    ) -> None:
        """The tag is what files it. A number guessed into the path gets
        a 404 rather than somebody else's paper."""
        from tests._pdf import pdf_bytes

        page, item_id = self._item(client, "MA-DOC-3")
        client.post(
            f"/matters/requests/items/{item_id}/attach",
            files={"file": ("mine.pdf", pdf_bytes(["Mine"]), "application/pdf")},
        )
        door = one(client.get(page).text, "#matter-requests [data-document]")
        document_id = door.get("hx-get").rsplit("/", 1)[-1]

        elsewhere = _matter(client, "MA-DOC-4").rsplit("/", 1)[-1]
        assert (
            client.get(f"/matters/{elsewhere}/documents/{document_id}").status_code
            == 404
        )

    def test_a_filed_document_can_be_linked_instead(self, client: TestClient) -> None:
        from tests._pdf import pdf_bytes

        page, item_id = self._item(client, "MA-DOC-5")
        client.post(
            f"/matters/requests/items/{item_id}/attach",
            files={"file": ("first.pdf", pdf_bytes(["First"]), "application/pdf")},
        )
        door = one(client.get(page).text, "#matter-requests [data-document]")
        document_id = door.get("hx-get").rsplit("/", 1)[-1]

        _, second = self._item(client, "MA-DOC-6")
        chooser = client.get(f"/matters/requests/items/{second}/attach").text
        assert document_id in [
            el.get("value") for el in select(chooser, "button[name=document_id]")
        ]

        client.post(
            f"/matters/requests/items/{second}/attach",
            data={"document_id": document_id},
        )
        drawn = client.get(f"/matters/requests/items/{second}/attach").text
        assert "first.pdf" in drawn

    def test_taking_the_paper_away_leaves_the_item_standing(
        self, client: TestClient
    ) -> None:
        """Wrong paper. An item whose only evidence has been taken away
        is not answered."""
        from tests._pdf import pdf_bytes

        page, item_id = self._item(client, "MA-DOC-7")
        client.post(
            f"/matters/requests/items/{item_id}/attach",
            files={"file": ("wrong.pdf", pdf_bytes(["Wrong"]), "application/pdf")},
        )

        back = client.post(f"/matters/requests/items/{item_id}/detach")

        assert text(one(back.text, "[data-standing]")) == "0 of 1"
        none(back.text, "[data-document]")

    def test_attaching_nothing_is_refused(self, client: TestClient) -> None:
        _page, item_id = self._item(client, "MA-DOC-8")
        answer = client.post(f"/matters/requests/items/{item_id}/attach", data={})
        assert answer.status_code == 400


class TestWhatWeCanSay:
    """A figure is not an answer until it says where it came from."""

    def _party(self, client: TestClient, name: str) -> str:
        _party(client, name, "person")
        people = client.get(f"/contacts?q={name}").text
        return str(
            select(people, "#contacts tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

    def test_a_daily_rate_is_kept_as_quoted_and_read_as_a_month(
        self, client: TestClient
    ) -> None:
        """The portal quotes a day and the county asks for a month.
        Multiplying on the way in would file our arithmetic as their
        quotation."""
        page = _matter(client, "MA-FACT-1")
        subject = self._party(client, "Fact Subject One")

        client.post(
            page + "/facts/new",
            data={
                "subject_party_id": subject,
                "attribute": "gross_income",
                "label": "IBEW pension",
                "amount": "$50.00",
                "period": "day",
                "as_of": "2026-08-01",
                "provenance": "stated",
                "source_note": "Read off the pension portal",
            },
        )

        drawn = client.get(page).text
        row = one(drawn, "#matter-facts [data-fact]")
        assert text(one(row, "[data-what]")) == "IBEW pension"
        assert "$50.00" in text(one(row, "[data-value]"))
        assert "$1,521.88" in text(one(row, "[data-monthly]"))
        assert "Read off the pension portal" in text(row)

    def test_two_sources_can_disagree_and_both_stand(self, client: TestClient) -> None:
        page = _matter(client, "MA-FACT-2")
        subject = self._party(client, "Fact Subject Two")
        for amount, provenance in (("2075.00", "ledger"), ("2180.40", "stated")):
            client.post(
                page + "/facts/new",
                data={
                    "subject_party_id": subject,
                    "attribute": "gross_income",
                    "label": "Social security",
                    "amount": amount,
                    "period": "month",
                    "as_of": "2026-08-01",
                    "provenance": provenance,
                },
            )

        rows = select(client.get(page).text, "#matter-facts [data-fact]")
        assert len(rows) == 2
        assert {text(one(row, "[data-value]")).strip().split()[0] for row in rows} == {
            "$2,075.00",
            "$2,180.40",
        }

    def test_a_figure_can_be_taken_off_again(self, client: TestClient) -> None:
        """``FactService.forget`` and its route shipped with ST-06 and
        nothing on the page ever called them, so a figure recorded by
        mistake - or the second of two identical ones - stood forever.
        A record you can fill and cannot empty accumulates every mistake
        ever made in it."""
        page = _matter(client, "MA-FACT-4")
        subject = self._party(client, "Fact Subject Four")
        client.post(
            page + "/facts/new",
            data={
                "subject_party_id": subject,
                "attribute": "gross_income",
                "label": "Typed twice",
                "amount": "2178.94",
                "period": "month",
                "provenance": "stated",
            },
        )
        row = one(client.get(page).text, "#matter-facts [data-fact]")

        # The control asks first: there is no undo, and the row beside it
        # looks identical when the duplicate is what you are removing.
        asked = client.get(one(row, "[data-forget]").get("hx-get"))
        assert asked.status_code == 200
        assert "Typed twice" in asked.text
        # In the app's own word for the thing. It said "figure", which is
        # what a fact CARRIES, on the page that calls them facts.
        from app.services.matters.words import word

        assert word("fact") in text(one(asked.text, "h2, h3, [data-dialog-title]"))
        # And it asks in a column: a question with six words in it drawn
        # across a 4xl dialog is a line the eye has to travel.
        one(asked.text, "[data-narrow]")

        gone = client.post(one(asked.text, "[hx-post]").get("hx-post"))
        assert gone.status_code == 200
        assert select(client.get(page).text, "#matter-facts [data-fact]") == []

    def test_a_reading_can_be_marked_as_checked(self, client: TestClient) -> None:
        """A citation proves where text came from, not that the reading
        was right. The dot that says somebody went and looked had no
        control to turn it on."""
        page = _matter(client, "MA-FACT-5")
        subject = self._party(client, "Fact Subject Five")
        client.post(
            page + "/facts/new",
            data={
                "subject_party_id": subject,
                "attribute": "gross_income",
                "label": "Checked by hand",
                "amount": "100.00",
                "period": "month",
                "provenance": "stated",
            },
        )
        row = one(client.get(page).text, "#matter-facts [data-fact]")
        assert text(one(row, "[data-verify]")) == "Checked"

        client.post(one(row, "[data-verify]").get("hx-post"))

        after = one(client.get(page).text, "#matter-facts [data-fact]")
        assert text(one(after, "[data-verify]")) == "Uncheck"

    def test_a_fact_with_nothing_in_it_is_refused(self, client: TestClient) -> None:
        page = _matter(client, "MA-FACT-3")
        subject = self._party(client, "Fact Subject Three")

        answer = client.post(
            page + "/facts/new",
            data={"subject_party_id": subject, "attribute": "gross_income"},
        )

        assert answer.status_code == 422
        assert "figure" in answer.text
        assert select(client.get(page).text, "#matter-facts [data-fact]") == []


class TestTheShelf:
    def test_paper_lands_on_the_case_without_answering_anything(
        self, client: TestClient
    ) -> None:
        """Paper arrives before anybody knows which ask it answers, and a
        shelf you cannot put a document on is a shelf nobody uses."""
        from tests._pdf import pdf_bytes

        page = _matter(client, "MA-SHELF-1")

        client.post(
            page + "/documents/new",
            files={
                "file": (
                    "award-letter.pdf",
                    pdf_bytes(["Award letter"]),
                    "application/pdf",
                )
            },
        )

        drawn = client.get(page).text
        shelf = one(drawn, "#card-paper-on-this-matter")
        assert "award-letter.pdf" in text(shelf)
        # Filed, but answering nothing yet.
        assert select(drawn, "#matter-requests [data-document]") == []


class TestSignIns:
    """Managing somebody's affairs means logging in as them. What this
    replaces is the sticky note beside the laptop."""

    def _party(self, client: TestClient, name: str) -> str:
        _party(client, name, "person")
        people = client.get(f"/contacts?q={name}").text
        return str(
            select(people, "#contacts tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

    def test_a_password_is_stored_shown_only_when_asked_for(
        self, client: TestClient
    ) -> None:
        party_id = self._party(client, "Signin Subject One")

        added = client.post(
            f"/contacts/{party_id}/signins/new",
            data={
                "label": "IBEW pension portal",
                "url": "pensionportal.example.com",
                "username": "jbedner",
                "secret": "correct-horse",
                "note": "",
            },
        )

        # The listing knows there is one and does not say what it is.
        assert "correct-horse" not in added.text
        row = one(added.text, "#sign-ins [data-signin]")
        assert text(one(row, "[data-label]")) == "IBEW pension portal"
        assert one(row, "[data-site]").get("href") == (
            "https://pensionportal.example.com"
        )

        shown = client.post(f"/contacts/signins/{row.get('data-signin')}/reveal")
        assert text(one(shown.text, "[data-secret]")) == "correct-horse"

        # And it is gone again on the next draw.
        again = client.get(f"/contacts/{party_id}/signins")
        assert select(again.text, "[data-secret]") == []

    def test_editing_the_username_keeps_the_password(self, client: TestClient) -> None:
        party_id = self._party(client, "Signin Subject Two")
        added = client.post(
            f"/contacts/{party_id}/signins/new",
            data={"label": "Portal", "username": "old", "secret": "keep-me"},
        )
        sign_in_id = one(added.text, "#sign-ins [data-signin]").get("data-signin")

        client.post(
            f"/contacts/signins/{sign_in_id}",
            data={"label": "Portal", "username": "new", "secret": ""},
        )
        shown = client.post(f"/contacts/signins/{sign_in_id}/reveal")

        assert text(one(shown.text, "[data-username]")) == "new"
        assert text(one(shown.text, "[data-secret]")) == "keep-me"

    def test_a_sign_in_needs_a_name(self, client: TestClient) -> None:
        party_id = self._party(client, "Signin Subject Three")

        answer = client.post(
            f"/contacts/{party_id}/signins/new",
            data={"label": " ", "secret": "x"},
        )

        assert answer.status_code == 422
        assert "name" in answer.text
        assert select(answer.text, "#sign-ins [data-signin]") == []


def test_a_fact_links_the_site_it_was_read_off(client: TestClient) -> None:
    """ "Read off the pension portal" is a note, not a way back."""
    page = _matter(client, "MA-URL-1")
    _party(client, "Url Subject", "person")
    people = client.get("/contacts?q=Url Subject").text
    subject = (
        select(people, "#contacts tbody [data-open]")[-1]
        .get("hx-get")
        .rsplit("/", 1)[-1]
    )

    client.post(
        page + "/facts/new",
        data={
            "subject_party_id": subject,
            "attribute": "gross_income",
            "label": "IBEW pension",
            "amount": "50.00",
            "period": "day",
            "provenance": "stated",
            "source_url": "pensionportal.example.com/benefits",
        },
    )

    site = one(client.get(page).text, "#matter-facts [data-site]")
    assert site.get("href") == "https://pensionportal.example.com/benefits"
    assert site.get("rel") == "noopener noreferrer"


def test_a_link_that_is_not_a_link_is_refused(client: TestClient) -> None:
    """A stored address is rendered into an href, and a javascript:
    href is a script the page runs."""
    page = _matter(client, "MA-URL-2")
    _party(client, "Url Subject Two", "person")
    people = client.get("/contacts?q=Url Subject Two").text
    subject = (
        select(people, "#contacts tbody [data-open]")[-1]
        .get("hx-get")
        .rsplit("/", 1)[-1]
    )

    answer = client.post(
        page + "/facts/new",
        data={
            "subject_party_id": subject,
            "attribute": "gross_income",
            "amount": "10.00",
            "source_url": "javascript:alert(1)",
        },
    )

    assert answer.status_code == 422
    assert "http or https" in answer.text


class TestAPlace:
    """A portal typed as free text is spelled three ways by the third
    sign-in. As a row it is the organization the matter already names."""

    def _org(self, client: TestClient, name: str, website: str) -> str:
        client.post(
            "/contacts/new",
            data={
                "name": name,
                "kind": "organization",
                "sort_name": "",
                "website": website,
                "note": "",
            },
        )
        people = client.get(f"/contacts?q={name}").text
        return str(
            select(people, "#contacts tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

    def test_a_fact_names_the_place_it_was_read_off(self, client: TestClient) -> None:
        page = _matter(client, "MA-PLACE-1")
        place = self._org(client, "Place Pension Fund", "placepension.example.com")
        _party(client, "Place Subject One", "person")
        people = client.get("/contacts?q=Place Subject One").text
        subject = (
            select(people, "#contacts tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

        # The place is offered, not typed.
        chooser = client.get(page + "/facts/new").text
        offered = [
            el.get("value")
            for el in select(chooser, "select[name=source_party_id] option")
        ]
        assert place in offered

        client.post(
            page + "/facts/new",
            data={
                "subject_party_id": subject,
                "attribute": "gross_income",
                "label": "Place pension",
                "amount": "50.00",
                "period": "day",
                "source_party_id": place,
            },
        )

        site = one(client.get(page).text, "#matter-facts [data-site]")
        assert text(site) == "Place Pension Fund"
        # No page was named, so the place's own website is the way back.
        assert site.get("href") == "https://placepension.example.com"

    def test_a_sign_in_points_at_the_same_place(self, client: TestClient) -> None:
        place = self._org(client, "Place Portal Co", "placeportal.example.com")
        _party(client, "Place Subject Two", "person")
        people = client.get("/contacts?q=Place Subject Two").text
        party_id = (
            select(people, "#contacts tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

        added = client.post(
            f"/contacts/{party_id}/signins/new",
            data={"label": "Portal", "site_party_id": place, "username": "jb"},
        )

        row = one(added.text, "#sign-ins [data-signin]")
        assert text(one(row, "[data-site]")) == "Place Portal Co"
        assert one(row, "[data-site]").get("href") == "https://placeportal.example.com"

    def test_a_website_that_is_not_a_link_is_refused(self, client: TestClient) -> None:
        answer = client.post(
            "/contacts/new",
            data={
                "name": "Bad Place Co",
                "kind": "organization",
                "sort_name": "",
                "website": "javascript:alert(1)",
                "note": "",
            },
        )

        assert answer.status_code == 422
        assert "http or https" in answer.text


def test_an_organization_lists_who_signs_in_there(client: TestClient) -> None:
    """The other end of the same row. The pension fund's page said "No
    sign-ins yet" while a sign-in pointed straight at it, because the
    list only ever read one side of the link."""
    client.post(
        "/contacts/new",
        data={
            "name": "Both Ends Fund",
            "kind": "organization",
            "sort_name": "",
            "website": "bothends.example.com",
            "note": "",
        },
    )
    _party(client, "Both Ends Owner", "person")
    people = client.get("/contacts?q=Both Ends").text
    ids = {
        text(el): el.get("hx-get").rsplit("/", 1)[-1]
        for el in select(people, "#contacts tbody [data-open]")
    }

    client.post(
        f"/contacts/{ids['Both Ends Owner']}/signins/new",
        data={
            "label": "Retirement Online",
            "site_party_id": ids["Both Ends Fund"],
            "username": "owner@",
        },
    )

    theirs = client.get(f"/contacts/{ids['Both Ends Fund']}/signins").text
    visitor = one(theirs, "[data-visitor]")
    assert "Both Ends Owner" in text(visitor)
    assert "Retirement Online" in text(visitor)


def test_a_mistyped_item_can_be_corrected(client: TestClient) -> None:
    """The sentence is the county's, not ours - which is exactly why a
    mistyped one has to be fixable. Answering the wrong question is
    worse than the typo."""
    page = _matter(client, "MA-EDIT-1")
    client.post(
        page + "/requests/new",
        data={"asked": "Proof of income for the IBEW and iHeart pensions"},
    )
    item = one(client.get(page).text, "#matter-requests [data-item]")
    item_id = item.get("data-item")

    answer = client.post(
        f"/matters/requests/items/{item_id}/edit",
        data={
            "asked": "Proof of GROSS monthly income for the IBEW pension",
            "ask": "fact:gross_income",
            "as_of": "",
        },
    )

    assert answer.status_code == 200
    asked = text(one(client.get(page).text, "#matter-requests [data-asked]"))
    assert asked == "Proof of GROSS monthly income for the IBEW pension"


def test_an_item_cannot_be_emptied(client: TestClient) -> None:
    page = _matter(client, "MA-EDIT-2")
    client.post(page + "/requests/new", data={"asked": "A copy of the POA"})
    item_id = one(client.get(page).text, "#matter-requests [data-item]").get(
        "data-item"
    )

    answer = client.post(
        f"/matters/requests/items/{item_id}/edit", data={"asked": "   "}
    )

    assert answer.status_code == 422
    assert "sentence that was asked" in answer.text


class TestTheStepsOfARequest:
    """A letter asks for four things and will take any one of three for
    the first. A list that draws them identically hides both facts."""

    def _request(self, client: TestClient, reference: str) -> tuple[str, str]:
        page = _matter(client, reference)
        client.post(page + "/requests/new", data={"asked": "A copy of the POA"})
        card = one(client.get(page).text, "#matter-requests [data-request]")
        return page, str(card.get("data-request"))

    def test_an_ask_can_be_added_after_the_letter_was_recorded(
        self, client: TestClient
    ) -> None:
        """Letters are read twice - and a request that can only be
        written at intake is one people keep a paper list beside."""
        page, request_id = self._request(client, "MA-STEP-1")

        client.post(
            f"/matters/requests/{request_id}/items/new",
            data={
                "asked": "Proof of gross income as of 1 July 2026",
                "kind": "figure",
                "ask": "fact:gross_income",
                "as_of": "2026-07-01",
            },
        )

        steps = select(client.get(page).text, "#matter-requests [data-step]")
        assert len(steps) == 2
        assert "Record a figure" in text(steps[1])
        assert text(one(client.get(page).text, "[data-standing]")) == "0 of 2"

    def test_alternatives_are_one_step_that_any_of_them_closes(
        self, client: TestClient
    ) -> None:
        """The county takes the POA, the designation OR the attestation.
        Three mandatory-looking rows describe a harder afternoon than
        the one you have."""
        page, request_id = self._request(client, "MA-STEP-2")
        first = select(client.get(page).text, "#matter-requests [data-item]")[0]

        client.post(
            f"/matters/requests/{request_id}/items/new",
            data={
                "asked": "A signed DOH-5247 designation",
                "kind": "form",
                "alternative_to": first.get("data-item"),
            },
        )

        drawn = client.get(page).text
        assert len(select(drawn, "#matter-requests [data-step]")) == 1
        assert "any one of 2" in text(one(drawn, "#matter-requests [data-step]"))
        assert text(one(drawn, "[data-standing]")) == "0 of 1"

        # Either one closes it.
        client.post(f"/matters/requests/items/{first.get('data-item')}/mark/satisfied")
        assert text(one(client.get(page).text, "[data-standing]")) == "1 of 1"

    def test_the_letter_sits_beside_the_asks_it_produced(
        self, client: TestClient
    ) -> None:
        from tests._pdf import pdf_bytes

        page, request_id = self._request(client, "MA-STEP-3")

        client.post(
            f"/matters/requests/{request_id}/letter",
            files={
                "file": (
                    "request.pdf",
                    pdf_bytes(["Request for information"]),
                    "application/pdf",
                )
            },
        )

        drawn = client.get(page).text
        assert one(drawn, "#matter-requests [data-letter]") is not None
        # The caption is the title, not the row it came from.
        assert (
            text(one(drawn, "#matter-requests [data-letter-title]")).strip()
            == "request.pdf"
        )

    def test_an_ask_needs_a_sentence(self, client: TestClient) -> None:
        _page, request_id = self._request(client, "MA-STEP-4")

        answer = client.post(
            f"/matters/requests/{request_id}/items/new", data={"asked": "  "}
        )

        assert answer.status_code == 422
        assert "sentence that was asked" in answer.text


def test_a_letter_can_be_worked_end_to_end_by_hand(client: TestClient) -> None:
    """The whole manual path in one pass, in the order a person walks it:
    the letter arrives, its asks are typed, one of them turns out to have
    an alternative, paper answers it, and a figure is recorded with the
    page it came from.

    Written because every piece of this was tested alone and the joins
    between them - does attaching close the STEP, does a fact recorded
    from the matter land on the matter - were not.
    """
    from tests._pdf import pdf_bytes

    page = _matter(client, "MA-WALK-1")

    # 1. What the letter demanded, as it worded it.
    client.post(
        page + "/requests/new",
        data={
            "asked": "A copy of the power of attorney",
            "due_on": "2026-09-08",
            "received_on": "2026-08-27",
        },
    )
    request_id = one(client.get(page).text, "[data-request]").get("data-request")

    # 2. The letter itself, cited: the page everything else is read against.
    client.post(
        f"/matters/requests/{request_id}/letter",
        files={"file": ("request.pdf", pdf_bytes(["Request"]), "application/pdf")},
    )

    # 3. A second ask, read off the same letter, needing a figure.
    client.post(
        f"/matters/requests/{request_id}/items/new",
        data={
            "asked": "Proof of gross income as of 1 July 2026",
            "kind": "figure",
            "as_of": "2026-07-01",
        },
    )

    # 4. The county will take a designation form INSTEAD of the POA.
    poa = select(client.get(page).text, "[data-step] [data-item]")[0].get("data-item")
    client.post(
        f"/matters/requests/{request_id}/items/new",
        data={
            "asked": "A signed DOH-5247 designation",
            "kind": "form",
            "alternative_to": poa,
        },
    )

    steps = select(client.get(page).text, "[data-step]")
    assert len(steps) == 2
    alternative = select(steps[0], "[data-item]")[1].get("data-item")

    # 5. Paper answers the alternative, which closes the whole step.
    client.post(
        f"/matters/requests/items/{alternative}/attach",
        files={"file": ("doh5247.pdf", pdf_bytes(["Designation"]), "application/pdf")},
    )

    drawn = client.get(page).text
    assert text(one(drawn, "[data-standing]")) == "1 of 2"
    assert one(drawn, '[data-step] [data-node="done"]') is not None
    assert one(drawn, "[data-letter]") is not None
    assert "doh5247.pdf" in text(select(drawn, "[data-step]")[0])

    # 6. And the figure the other step wants, with where it came from.
    _party(client, "Walk Subject", "person")
    subject = select(
        client.get("/contacts?q=Walk Subject").text, "#contacts tbody [data-open]"
    )
    client.post(
        page + "/facts/new",
        data={
            "subject_party_id": subject[-1].get("hx-get").rsplit("/", 1)[-1],
            "attribute": "gross_income",
            "label": "Walk pension",
            "amount": "1200.00",
            "period": "month",
            "as_of": "2026-07-01",
            "provenance": "stated",
            "source_note": "Read off the portal",
        },
    )

    said = one(client.get(page).text, "#matter-facts [data-fact]")
    assert "Walk pension" in text(said)
    assert "$1,200.00" in text(said)
    assert "Read off the portal" in text(said)


def test_a_matter_page_has_the_way_back(client: TestClient) -> None:
    """A page you navigate TO needs one: the sidebar takes you to the
    section, not to the list you came from."""
    page = _matter(client, "MA-BACK-1")

    back = one(client.get(page).text, "#matter [data-back]")

    assert back.get("href") == "/matters"
    assert text(back) == "Matters"


class TestNamingSomebodyWhereYouNeedThem:
    """A picker that only offers rows somebody already made sends the
    reader to another page mid-sentence, and they come back having lost
    the four fields they had typed."""

    def test_a_participant_can_be_named_on_the_matter(self, client: TestClient) -> None:
        page = _matter(client, "MA-INLINE-1")

        client.post(
            page + "/participants",
            data={"party_id": "", "new_name": "Inline Law PC", "role": "counsel"},
        )

        roles = {
            text(el): text(el.getparent().find_class("text-aegis-muted")[0])
            for el in select(client.get(page).text, "#matter [data-party]")
        }
        assert "Inline Law PC" in roles

    def test_a_fact_can_name_whose_money_it_is(self, client: TestClient) -> None:
        page = _matter(client, "MA-INLINE-2")

        client.post(
            page + "/facts/new",
            data={
                "subject_party_id": "",
                "new_subject": "Inline Subject",
                "attribute": "gross_income",
                "amount": "10.00",
                "period": "month",
                "provenance": "stated",
            },
        )

        said = one(client.get(page).text, "#matter-facts [data-fact]")
        assert "$10.00" in text(said)
        # And the person exists afterwards, in the one address book.
        people = client.get("/contacts?q=Inline Subject").text
        assert "Inline Subject" in text(one(people, "#contacts"))

    def test_the_typed_name_wins_over_the_list(self, client: TestClient) -> None:
        """Somebody who types a name after picking from the list has
        changed their mind about the list."""
        page = _matter(client, "MA-INLINE-3")
        _party(client, "Inline Picked", "person")
        picked = (
            select(
                client.get("/contacts?q=Inline Picked").text,
                "#contacts tbody [data-open]",
            )[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

        client.post(
            page + "/participants",
            data={
                "party_id": picked,
                "new_name": "Inline Typed",
                "role": "other",
            },
        )

        named = {
            text(el) for el in select(client.get(page).text, "#matter [data-party]")
        }
        assert "Inline Typed" in named
        assert "Inline Picked" not in named


def test_every_dialog_on_a_matter_renders(client: TestClient) -> None:
    """A template that imports one macro and still calls another breaks
    only when somebody opens it - which a test of the POST never does.
    "Add someone" shipped that way twice in one day: first with no route
    at all, then with a 500 behind the route.
    """
    page = _matter(client, "MA-DIALOGS-1")
    matter_id = page.rsplit("/", 1)[-1]
    client.post(page + "/requests/new", data={"asked": "A copy of the POA"})
    drawn = client.get(page).text
    request_id = one(drawn, "[data-request]").get("data-request")
    item_id = select(drawn, "[data-item]")[0].get("data-item")

    for url in (
        f"/matters/{matter_id}/participants/new",
        f"/matters/{matter_id}/requests/new",
        f"/matters/{matter_id}/facts/new",
        f"/matters/{matter_id}/documents/new",
        f"/matters/requests/{request_id}/items/new",
        f"/matters/requests/{request_id}/letter",
        f"/matters/requests/items/{item_id}/edit",
        f"/matters/requests/items/{item_id}/attach",
        "/matters/new",
    ):
        answer = client.get(url)
        assert answer.status_code == 200, f"{url} -> {answer.status_code}"
        # A dialog body, never a whole page swapped into the modal.
        none(answer.text, "html")


class TestTheVerbsOnAnAsk:
    """Two rows: what you do to the ask, and what you mark it as. The
    words come from one place, and they are the generic ones."""

    def test_doing_and_marking_are_separate_rows(self, client: TestClient) -> None:
        from app.services.matters.words import ITEM_VERBS, WORDS

        page = _matter(client, "MA-VERBS")
        client.post(
            page + "/requests/new",
            data={"asked": ASKED, "due_on": "2026-09-08", "received_on": ""},
        )
        first = select(client.get(page).text, "#matter-requests [data-item]")[0]
        rows = select(first, "[data-verbs] > div")
        assert len(rows) == 2
        doing = [text(b) for b in rows[0].findall(".//button")]
        marking = [text(b) for b in rows[1].findall(".//button")]
        assert doing == [WORDS["attach"], WORDS["edit"]]
        assert marking == [
            ITEM_VERBS["satisfied"],
            ITEM_VERBS["not_applicable"],
            ITEM_VERBS["waived"],
        ]
        assert rows[1].get("aria-label") == WORDS["mark_as"]


class TestOverdueIsRed:
    """Late is not "warn". A deadline that has passed is the one thing on
    the page that must not read as a caution: the card, the list and the
    sidebar all say it in red, from one query."""

    def _late(self, client: TestClient, reference: str) -> str:
        page = _matter(client, reference)
        client.post(
            page + "/requests/new",
            data={"asked": ASKED, "due_on": "2000-01-01", "received_on": ""},
        )
        return page

    def test_the_card_carries_a_red_dot(self, client: TestClient) -> None:
        page = self._late(client, "MA-LATE-1")
        card = one(client.get(page).text, "#matter-requests [data-request]")
        assert select(card, "header [data-dot=error]"), (
            "the due line carries the red dot"
        )

    def test_the_list_row_says_overdue(self, client: TestClient) -> None:
        self._late(client, "MA-LATE-2")
        rows = select(client.get("/matters").text, "#matters tbody tr")
        states = {
            text(one(r, "[data-tone]")): one(r, "[data-tone]").get("data-tone")
            for r in rows
        }
        assert states.get("overdue") == "error"

    def test_the_sidebar_fetches_a_mark_that_is_red_only_when_late(
        self, client: TestClient
    ) -> None:
        nav = one(client.get("/matters").text, "[data-attention]")
        assert nav.get("hx-get") == "/matters/attention"
        assert "load" in (nav.get("hx-trigger") or "")
        # Inside a boosted link, so it must override what it would inherit.
        assert nav.get("hx-target") == "this"
        assert nav.get("hx-push-url") == "false"
        # The app-owned database is shared across the run, so "nothing
        # overdue" cannot be asserted here; that the mark is red and
        # counts is enough.
        self._late(client, "MA-LATE-3")
        mark = one(client.get("/matters/attention").text, "[data-dot]")
        assert mark.get("data-dot") == "error"
        assert text(mark).strip().endswith("overdue")


class TestFilesCanBeDropped:
    def test_every_file_input_is_a_drop_and_paste_target(
        self, client: TestClient
    ) -> None:
        import json

        page = _matter(client, "MA-DROP")
        dialog = client.get(page + "/documents/new").text
        zone = one(dialog, "[data-dropzone]")
        assert zone.get("@drop.prevent"), "the drop lands in the input"
        assert zone.get("@paste.window"), "and so does the clipboard"
        # A click on the pane focuses it rather than opening the picker:
        # the native input is hidden and only the button reaches it.
        assert zone.get("tabindex") == "0"
        chooser = one(zone, "input[type=file]")
        assert "sr-only" in chooser.get("class")
        assert (
            select(zone, "button[type=button]")[0].get("@click.stop")
            == "$refs.file.click()"
        )
        # What landed is drawn with the store's own marks, sent as data.
        table = json.loads(zone.get("data-badges"))
        assert table["kinds"]["pdf"]["label"] == "PDF"
        one(zone, "[data-chosen] template[x-for]")
        one(zone, "[data-chosen] button[aria-label=Remove]")


class TestAContactSeesItsCases:
    """From the other side: a participant's page names the case and the
    role. Here rather than with the contact tests because the matters
    list's empty-state test must see no matters first."""

    def test_the_cases_they_are_in_say_as_what(self, client: TestClient) -> None:
        from tests.web.test_contacts import _contact

        party_id = _contact(client, "Dutchess DSS (contact page)", "organization")
        matter_page = _matter(client, "MA-CONTACT-1")
        client.post(
            matter_page + "/participants",
            data={
                "party_id": str(party_id),
                "new_name": "",
                "role": "agency",
                "note": "",
            },
        )
        page = client.get(f"/contacts/{party_id}").text
        case = one(page, "[data-cases] li")
        assert "Medicaid renewal" in text(case)
        assert text(one(case, "[data-role]")) == "Agency"

    def test_on_the_matter_its_participants_and_the_list(
        self, client: TestClient
    ) -> None:
        from tests.web.test_contacts import _contact

        party_id = _contact(client, "Door County DSS", "organization")
        matter_page = _matter(client, "MA-DOOR-1")
        client.post(
            matter_page + "/participants",
            data={
                "party_id": str(party_id),
                "new_name": "",
                "role": "agency",
                "note": "",
            },
        )
        page = client.get(matter_page).text
        door = one(page, f'#matter [data-party] a[data-contact="{party_id}"]')
        assert door.get("href") == f"/contacts/{party_id}"
        assert door.get("hx-get") == f"/contacts/{party_id}"
