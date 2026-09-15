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
        answer = client.post(
            "/matters/new",
            data={
                "title": "  ",
                "kind": "",
                "reference": "",
                "subject_party_id": "",
                "counterpart_party_id": "",
                "opened_on": "",
            },
        )
        assert answer.status_code == 422
        assert "needs a title" in answer.text
        assert "No matters yet" in client.get("/matters").text


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
