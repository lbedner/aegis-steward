"""The Documents section: everything on file, and what was read off it.

Both render paths, selectors not substrings.
"""

from fastapi.testclient import TestClient

from tests._pdf import pdf_bytes
from tests.web.dom import none, one, select, text, triggers


def _file(client: TestClient, title: str = "letter.pdf") -> int:
    answer = client.post(
        "/documents/new",
        files={
            # Unique bytes per title: the store dedupes by content, and two
            # uploads of one file are one document however they were named.
            "file": (
                title,
                pdf_bytes([f"Request for information: {title}"]),
                "application/pdf",
            )
        },
        data={"kind": "letter"},
    )
    assert answer.status_code == 200, answer.text
    # By title: the shelf is shared by every worker of the run and shows
    # a page of 200, so an unnarrowed list can have pushed this one off.
    rows = select(
        client.get("/documents", params={"q": title}).text, "#documents tbody tr"
    )
    row = next(r for r in rows if title in text(r))
    return int(row.get("id").split("-")[-1])


class TestTheShelf:
    def test_the_page_renders_and_is_a_records_section(
        self, client: TestClient
    ) -> None:
        answer = client.get("/documents")
        assert answer.status_code == 200, answer.text[-1500:]
        page = answer.text
        one(page, "#documents")
        assert one(page, 'a[aria-current="page"]').get("href") == "/documents"

    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/documents").text, "html")

    def test_a_filed_document_is_listed_with_its_kind(self, client: TestClient) -> None:
        document_id = _file(client, "renewal.pdf")
        row = one(client.get("/documents").text, f"tr#document-{document_id}")
        assert "letter" in text(row)

    def test_the_search_narrows_where_it_stands(self, client: TestClient) -> None:
        _file(client, "needle.pdf")
        _file(client, "hay.pdf")
        page = client.get("/documents", params={"q": "needle"}).text
        titles = [text(t) for t in select(page, "#documents tbody tr")]
        assert any("needle.pdf" in t for t in titles)
        assert not any("hay.pdf" in t for t in titles)
        form = one(page, "[data-filters]")
        assert form.get("hx-get") == "/documents"


class TestTheDialog:
    async def test_it_shows_how_each_page_was_read(self, client: TestClient) -> None:
        from app.services.documents.domains.extraction.jobs import run_extraction

        document_id = _file(client)
        # Read here rather than on the worker: the test wants the pages, not the job.
        await run_extraction(
            document_id, owner_user_id=None, force=False, report=lambda _: None
        )
        dialog = client.get(f"/documents/{document_id}").text
        one(dialog, "[data-original]")
        how = text(one(dialog, "[data-page='1'] [data-how]"))
        assert how.startswith("Page 1 · Text layer")

    def test_read_again_goes_to_the_worker_and_says_so(
        self, client: TestClient, monkeypatch
    ) -> None:
        import app.components.web_frontend.routes.documents as routes

        queued: list[tuple[int, bool]] = []

        async def enqueue(document_id: int, *, owner_user_id, force: bool) -> str:
            queued.append((document_id, force))
            return "job"

        monkeypatch.setattr(routes, "start_extraction", enqueue)
        document_id = _file(client)
        dialog = client.get(f"/documents/{document_id}").text
        button = one(dialog, f'button[hx-post="/documents/{document_id}/read"]')
        assert button.get("hx-swap") == "none"

        answer = client.post(f"/documents/{document_id}/read")

        assert answer.status_code == 204
        assert queued == [(document_id, True)]
        assert "toast" in triggers(answer)

    def test_saving_returns_where_you_were(self, client: TestClient) -> None:
        document_id = _file(client)
        answer = client.post(
            f"/documents/{document_id}",
            data={
                "title": "Renewal letter",
                "kind": "letter",
                "document_date": "",
                "note": "",
            },
            headers={"HX-Current-URL": "http://testserver/documents"},
        )
        assert answer.status_code == 200
        shelf = client.get("/documents", params={"q": "Renewal letter"}).text
        row = one(shelf, f"tr#document-{document_id}")
        assert "Renewal letter" in text(row)


class TestWhereItWasFiled:
    def test_the_opener_is_not_starved_by_the_filter_form(
        self, client: TestClient
    ) -> None:
        """The Add button sits inside the search form, and htmx hands every
        inherited attribute down: the form's ``hx-select`` narrowed the
        dialog's answer to an element it does not have, and the modal
        opened empty. A filter form keeps its attributes to itself."""
        form = one(client.get("/documents").text, "#documents form[data-filters]")
        assert "hx-select" in form.get("hx-disinherit", "")

    def test_filed_under_names_the_place_and_goes_there(
        self, client: TestClient
    ) -> None:
        """A tag is a key; a reader wants the contact's name, as a door."""
        from tests.web.test_contacts import _contact

        party_id = _contact(client, "Filed Under Testcase", "organization")
        client.post(
            f"/contacts/{party_id}/documents/new",
            files={
                "file": (
                    "filed-under.pdf",
                    pdf_bytes(["Filed under a contact"]),
                    "application/pdf",
                )
            },
        )
        shelf = client.get("/documents", params={"q": "filed-under"}).text
        row = next(
            r
            for r in select(shelf, "#documents tbody tr")
            if "filed-under.pdf" in text(r)
        )
        door = one(row, f'a[href="/contacts/{party_id}"]')
        assert "Filed Under Testcase" in text(door)
        assert "party:" not in text(row)

        opener = one(row, "[data-open]")
        dialog = client.get(opener.get("hx-get")).text
        one(dialog, f'[data-filed] a[href="/contacts/{party_id}"]')


class TestFilingItSomewhere:
    def test_the_dialog_files_a_document_under_a_contact_and_takes_it_off(
        self, client: TestClient
    ) -> None:
        """Where a document is filed is a field on its form: every place
        offered, the chosen ones as chips, saved with the rest."""
        import json

        from tests.web.test_contacts import _contact

        party_id = _contact(client, "Filing Testcase", "organization")
        document_id = _file(client, "filing.pdf")
        dialog = client.get(f"/documents/{document_id}").text
        chips = one(dialog, 'form[data-details] [data-chips="place"]')
        offered = {o["id"]: o["name"] for o in json.loads(chips.get("data-options"))}
        assert offered[f"party:{party_id}"].startswith("Filing Testcase")
        assert json.loads(chips.get("data-chosen")) == []

        said = {
            "title": "filing.pdf",
            "kind": "other",
            "document_date": "",
            "note": "",
            "place_sent": "1",
        }
        filed = client.post(
            f"/documents/{document_id}",
            data={**said, "place": [f"party:{party_id}"]},
            headers={"HX-Current-URL": "http://testserver/documents"},
        )
        assert filed.status_code == 200
        dialog = client.get(f"/documents/{document_id}").text
        one(dialog, f'[data-filed] a[href="/contacts/{party_id}"]')
        chips = one(dialog, '[data-chips="place"]')
        assert [c["id"] for c in json.loads(chips.get("data-chosen"))] == [
            f"party:{party_id}"
        ]
        assert "filing.pdf" in text(
            one(client.get(f"/contacts/{party_id}").text, "#contact-paper")
        )

        gone = client.post(
            f"/documents/{document_id}",
            data=said,
            headers={"HX-Current-URL": "http://testserver/documents"},
        )
        assert gone.status_code == 200
        none(client.get(f"/documents/{document_id}").text, "[data-filed]")

    def test_a_form_without_the_field_refiles_nothing(self, client: TestClient) -> None:
        """A save that did not carry the chips leaves the filing alone."""
        from tests.web.test_contacts import _contact

        party_id = _contact(client, "Keep Filing Testcase", "organization")
        document_id = _file(client, "keep-filing.pdf")
        client.post(
            f"/documents/{document_id}",
            data={
                "title": "keep-filing.pdf",
                "kind": "other",
                "document_date": "",
                "note": "",
                "place_sent": "1",
                "place": [f"party:{party_id}"],
            },
        )
        client.post(
            f"/documents/{document_id}",
            data={
                "title": "keep-filing.pdf",
                "kind": "other",
                "document_date": "",
                "note": "",
            },
        )
        one(
            client.get(f"/documents/{document_id}").text,
            f'[data-filed] a[href="/contacts/{party_id}"]',
        )


class TestManyPlacesFold:
    def test_a_cell_shows_two_and_folds_the_rest(self, client: TestClient) -> None:
        from tests.web.test_contacts import _contact

        ids = [_contact(client, f"Fold Testcase {n}", "person") for n in range(4)]
        document_id = _file(client, "fold.pdf")
        client.post(
            f"/documents/{document_id}",
            data={
                "title": "fold.pdf",
                "kind": "other",
                "document_date": "",
                "note": "",
                "place_sent": "1",
                "place": [f"party:{i}" for i in ids],
            },
        )
        shelf = client.get("/documents", params={"q": "fold.pdf"}).text
        row = one(shelf, f"tr#document-{document_id}")
        assert len(select(row, "a[href^='/contacts/']")) == 4
        assert text(one(row, "[data-more]")) == "+2 more"
        assert len(select(row, "[x-show='all']")) == 2


class TestReadingRatherThanEditing:
    """``?reading=1`` is how an approval card opens the paper it read."""

    def test_the_form_is_gone_and_the_pages_are_not(
        self, client: TestClient
    ) -> None:
        filed = _file(client, "reading.pdf")
        editing = client.get(f"/documents/{filed}").text
        one(editing, "form[data-details]")

        reading = client.get(f"/documents/{filed}?reading=1").text
        assert none(reading, "form[data-details]") is None
        one(reading, "[data-details][data-read-only]")
        # The half worth opening it for is still there.
        one(reading, "[data-original]")

    def test_read_again_is_not_offered(self, client: TestClient) -> None:
        """Reading again is what MAKES the card that is waiting."""
        filed = _file(client, "reading-again.pdf")
        reading = client.get(f"/documents/{filed}?reading=1").text
        assert not [
            el for el in select(reading, "[hx-post]") if "/read" in (el.get("hx-post") or "")
        ]
