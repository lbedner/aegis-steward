"""The Documents section: everything on file, and what was read off it.

Both render paths, selectors not substrings.
"""

from fastapi.testclient import TestClient
import pytest

from tests._pdf import pdf_bytes
from tests.web.dom import location, none, one, select, text, triggers


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


class TestDelete:
    """The only delete was ``DELETE /api/v1/documents/{id}`` - soft, and
    nothing in the frontend called it. So two logos a mail import filed
    by mistake had no way out short of SQL (2026-09-21)."""

    def test_the_dialog_offers_delete_through_a_confirm(
        self, client: TestClient
    ) -> None:
        document_id = _file(client, "junk.pdf")
        dialog = client.get(f"/documents/{document_id}").text
        opener = one(dialog, "[data-delete]")
        assert opener.get("hx-get") == f"/documents/{document_id}/delete"

        confirm = client.get(f"/documents/{document_id}/delete").text
        button = one(confirm, f'button[hx-delete="/documents/{document_id}"]')
        assert button.get("hx-swap") == "none"

    def test_deleting_takes_it_off_the_shelf_and_closes_the_dialog(
        self, client: TestClient
    ) -> None:
        document_id = _file(client, "gone.pdf")

        answer = client.delete(
            f"/documents/{document_id}",
            headers={"HX-Current-URL": "http://t/documents"},
        )

        assert answer.status_code == 200
        assert "dialog:close" in triggers(answer)
        assert location(answer) == "/documents"
        rows = select(
            client.get("/documents", params={"q": "gone.pdf"}).text,
            "#documents tbody tr",
        )
        assert not any("gone.pdf" in text(r) for r in rows)
        assert client.get(f"/documents/{document_id}").status_code == 404

    @pytest.mark.asyncio
    async def test_a_protected_document_is_not_offered_it(
        self, client: TestClient
    ) -> None:
        """Protection means the exact title typed back, and the confirm
        has no field for it; the API door does. Not offering beats a
        button that answers with an error."""
        from app.core.db import get_async_session
        from app.services.documents import DocumentService

        document_id = _file(client, "deed.pdf")
        # The routes open their own sessions (app-owned engine); mark it
        # there, not on the per-test session the API would see.
        async with get_async_session() as db:
            await DocumentService(db).update(document_id, {"protected": True})
            await db.commit()

        dialog = client.get(f"/documents/{document_id}").text
        none(dialog, "[data-delete]")


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
        # The progress lands beside the button rather than nowhere: this
        # answered 204 and the dialog sat there looking broken while six
        # pages were read (2026-09-19).
        assert button.get("hx-swap") == "innerHTML"
        assert button.get("hx-target") == "#reading-progress"

        answer = client.post(f"/documents/{document_id}/read")

        assert answer.status_code == 200
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

    def test_the_form_is_gone_and_the_pages_are_not(self, client: TestClient) -> None:
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
            el
            for el in select(reading, "[hx-post]")
            if "/read" in (el.get("hx-post") or "")
        ]


class TestPaperIsReadWhenItArrives:
    """Nothing read an uploaded document. A read only happened if
    somebody clicked "Read again", called the API, or asked her - so
    paper landed on the shelf saying nothing about itself, and the card
    that would name it was never made (2026-09-19).
    """

    def test_uploading_starts_the_read(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.documents.domains.extraction import dispatch

        started: list[tuple[int, bool]] = []

        async def fake(document_id: int, *, owner_user_id=None, force=False) -> str:
            started.append((document_id, force))
            return "job-1"

        # The guard (read_quietly) lives in dispatch now and looks this up there.
        monkeypatch.setattr(dispatch, "start_extraction", fake)
        document_id = _file(client, "arrives.pdf")

        # Not forced: a page is read once, and an upload of a document
        # already on the shelf must not pay to read it twice.
        assert started == [(document_id, False)]

    def test_a_read_that_will_not_start_still_files_the_paper(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bytes are the valuable thing. A worker that is down loses
        the reading, never the document."""
        from app.services.documents.domains.extraction import dispatch

        async def broken(document_id: int, *, owner_user_id=None, force=False) -> str:
            raise RuntimeError("no worker today")

        monkeypatch.setattr(dispatch, "start_extraction", broken)
        document_id = _file(client, "no-worker.pdf")

        row = one(client.get("/documents").text, f"tr#document-{document_id}")
        assert "no-worker.pdf" in text(row)


class TestReadingAgainShowsItself:
    """Reading a document is worker work: six pages of a scan is a model
    call each. It answered with a 204 and a toast, so the dialog sat
    there looking broken while the pages were being read - and there was
    no way to tell a job that had started from one that had not
    (2026-09-19).

    Pattern 5 exists for exactly this and extraction is its most obvious
    user: the route had the job id and threw it away.
    """

    def test_it_hands_back_a_live_job(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.components.web_frontend.routes import documents as routes

        async def fake(document_id, *, owner_user_id=None, force=False) -> str:
            return "job-42"

        monkeypatch.setattr(routes, "start_extraction", fake)
        document_id = _file(client, "again.pdf")

        answer = client.post(f"/documents/{document_id}/read")

        assert answer.status_code == 200
        following = one(answer.text, "[sse-connect]")
        assert following.get("sse-connect") == "/jobs/job-42/events"
        # And it says what it is doing while it does it.
        assert "again.pdf" in text(following)

    def test_the_button_has_somewhere_to_put_it(self, client: TestClient) -> None:
        """A follower nobody swaps in is a 204 with extra steps."""
        document_id = _file(client, "target.pdf")
        dialog = client.get(f"/documents/{document_id}").text

        button = one(dialog, f'[hx-post="/documents/{document_id}/read"]')
        assert button.get("hx-target") == "#reading-progress"
        one(dialog, "#reading-progress")

    def test_a_read_that_runs_inline_still_answers(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No worker is a normal install, not an error: the read happens
        in the request and the answer says it is done."""
        from app.components.web_frontend.routes import documents as routes

        async def inline(document_id, *, owner_user_id=None, force=False) -> None:
            return None

        monkeypatch.setattr(routes, "start_extraction", inline)
        document_id = _file(client, "inline-again.pdf")

        answer = client.post(f"/documents/{document_id}/read")

        assert answer.status_code == 200
        assert none(answer.text, "[sse-connect]") is None
        assert "inline-again.pdf" in answer.text


class TestTheNameItArrivedWith:
    """Renaming a document must not lose what the bank called it: six
    months later somebody searches for the download's own name."""

    def test_the_shelf_still_finds_it_by_its_filename(self, client: TestClient) -> None:
        document_id = _file(client, "AP_DRTNY107_2354889.pdf")
        client.post(
            f"/documents/{document_id}",
            data={
                "title": "Delta Dental Application",
                "kind": "form",
                "document_date": "",
                "note": "",
            },
        )

        page = client.get("/documents", params={"q": "AP_DRTNY107"}).text
        row = one(page, f"tr#document-{document_id}")
        assert "Delta Dental Application" in text(row)

    def test_the_dialog_says_what_it_arrived_as(self, client: TestClient) -> None:
        """Quietly, under the name we gave it - the way a register row
        keeps its raw descriptor under the payee."""
        document_id = _file(client, "scan0007.pdf")
        client.post(
            f"/documents/{document_id}",
            data={
                "title": "Dad's POA",
                "kind": "other",
                "document_date": "",
                "note": "",
            },
        )

        dialog = client.get(f"/documents/{document_id}").text
        assert "scan0007.pdf" in text(one(dialog, "[data-arrived]"))

    def test_a_document_nobody_renamed_says_it_once(self, client: TestClient) -> None:
        """The filename under a title that IS the filename is the same
        word twice."""
        document_id = _file(client, "untouched.pdf")
        dialog = client.get(f"/documents/{document_id}").text
        assert none(dialog, "[data-arrived]") is None
