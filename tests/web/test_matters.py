"""The Matters section: cases, and who is in them.

ST-03's UI half. Both render paths, selectors not substrings.
"""

from fastapi.testclient import TestClient

from tests.web.dom import none, one, select, text


def _party(client: TestClient, name: str, kind: str) -> None:
    client.post(
        "/settings/people/new",
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
        people = client.get("/settings/people").text
        ids = [
            el.get("hx-get").rsplit("/", 1)[-1]
            for el in select(people, "#people tbody [data-open]")
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

        answer = client.post(f"/matters/requests/items/{items[0].get('data-item')}/mark/satisfied")

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
        back = client.post(f"/matters/requests/items/{item.get('data-item')}/mark/needed")

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

        answer = client.post(f"/matters/requests/items/{item.get('data-item')}/mark/lost")

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

    def test_adding_one_files_it_and_answers_the_item(
        self, client: TestClient
    ) -> None:
        from tests._pdf import pdf_bytes

        page, item_id = self._item(client, "MA-DOC-1")
        base = f"/matters/requests/items/{item_id}"

        answer = client.post(
            base + "/attach",
            files={"file": ("poa.pdf", pdf_bytes(["Power of attorney"]), "application/pdf")},
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
            files={"file": ("poa.pdf", pdf_bytes(["Power of attorney"]), "application/pdf")},
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
        people = client.get(f"/settings/people?q={name}").text
        return str(
            select(people, "#people tbody [data-open]")[-1]
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

    def test_two_sources_can_disagree_and_both_stand(
        self, client: TestClient
    ) -> None:
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
        people = client.get(f"/settings/people?q={name}").text
        return str(
            select(people, "#people tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

    def test_a_password_is_stored_shown_only_when_asked_for(
        self, client: TestClient
    ) -> None:
        party_id = self._party(client, "Signin Subject One")

        added = client.post(
            f"/settings/people/{party_id}/signins/new",
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

        shown = client.post(
            f"/settings/people/signins/{row.get('data-signin')}/reveal"
        )
        assert text(one(shown.text, "[data-secret]")) == "correct-horse"

        # And it is gone again on the next draw.
        again = client.get(f"/settings/people/{party_id}/signins")
        assert select(again.text, "[data-secret]") == []

    def test_editing_the_username_keeps_the_password(
        self, client: TestClient
    ) -> None:
        party_id = self._party(client, "Signin Subject Two")
        added = client.post(
            f"/settings/people/{party_id}/signins/new",
            data={"label": "Portal", "username": "old", "secret": "keep-me"},
        )
        sign_in_id = one(added.text, "#sign-ins [data-signin]").get("data-signin")

        client.post(
            f"/settings/people/signins/{sign_in_id}",
            data={"label": "Portal", "username": "new", "secret": ""},
        )
        shown = client.post(f"/settings/people/signins/{sign_in_id}/reveal")

        assert text(one(shown.text, "[data-username]")) == "new"
        assert text(one(shown.text, "[data-secret]")) == "keep-me"

    def test_a_sign_in_needs_a_name(self, client: TestClient) -> None:
        party_id = self._party(client, "Signin Subject Three")

        answer = client.post(
            f"/settings/people/{party_id}/signins/new",
            data={"label": " ", "secret": "x"},
        )

        assert answer.status_code == 422
        assert "name" in answer.text
        assert select(answer.text, "#sign-ins [data-signin]") == []


def test_a_fact_links_the_site_it_was_read_off(client: TestClient) -> None:
    """"Read off the pension portal" is a note, not a way back."""
    page = _matter(client, "MA-URL-1")
    _party(client, "Url Subject", "person")
    people = client.get("/settings/people?q=Url Subject").text
    subject = (
        select(people, "#people tbody [data-open]")[-1]
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
    people = client.get("/settings/people?q=Url Subject Two").text
    subject = (
        select(people, "#people tbody [data-open]")[-1]
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
            "/settings/people/new",
            data={
                "name": name,
                "kind": "organization",
                "sort_name": "",
                "website": website,
                "note": "",
            },
        )
        people = client.get(f"/settings/people?q={name}").text
        return str(
            select(people, "#people tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

    def test_a_fact_names_the_place_it_was_read_off(
        self, client: TestClient
    ) -> None:
        page = _matter(client, "MA-PLACE-1")
        place = self._org(client, "Place Pension Fund", "placepension.example.com")
        _party(client, "Place Subject One", "person")
        people = client.get("/settings/people?q=Place Subject One").text
        subject = (
            select(people, "#people tbody [data-open]")[-1]
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
        people = client.get("/settings/people?q=Place Subject Two").text
        party_id = (
            select(people, "#people tbody [data-open]")[-1]
            .get("hx-get")
            .rsplit("/", 1)[-1]
        )

        added = client.post(
            f"/settings/people/{party_id}/signins/new",
            data={"label": "Portal", "site_party_id": place, "username": "jb"},
        )

        row = one(added.text, "#sign-ins [data-signin]")
        assert text(one(row, "[data-site]")) == "Place Portal Co"
        assert one(row, "[data-site]").get("href") == "https://placeportal.example.com"

    def test_a_website_that_is_not_a_link_is_refused(
        self, client: TestClient
    ) -> None:
        answer = client.post(
            "/settings/people/new",
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
        "/settings/people/new",
        data={
            "name": "Both Ends Fund",
            "kind": "organization",
            "sort_name": "",
            "website": "bothends.example.com",
            "note": "",
        },
    )
    _party(client, "Both Ends Owner", "person")
    people = client.get("/settings/people?q=Both Ends").text
    ids = {
        text(el): el.get("hx-get").rsplit("/", 1)[-1]
        for el in select(people, "#people tbody [data-open]")
    }

    client.post(
        f"/settings/people/{ids['Both Ends Owner']}/signins/new",
        data={
            "label": "Retirement Online",
            "site_party_id": ids["Both Ends Fund"],
            "username": "owner@",
        },
    )

    theirs = client.get(f"/settings/people/{ids['Both Ends Fund']}/signins").text
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
    item_id = one(client.get(page).text, "#matter-requests [data-item]").get("data-item")

    answer = client.post(
        f"/matters/requests/items/{item_id}/edit", data={"asked": "   "}
    )

    assert answer.status_code == 422
    assert "sentence that was asked" in answer.text
