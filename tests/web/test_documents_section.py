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
    rows = select(client.get("/documents").text, "#documents tbody tr")
    row = next(r for r in rows if title in text(r))
    return int(row.get("id").split("-")[-1])


class TestTheShelf:
    def test_the_page_renders_and_is_a_records_section(
        self, client: TestClient
    ) -> None:
        page = client.get("/documents").text
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
        row = one(client.get("/documents").text, f"tr#document-{document_id}")
        assert "Renewal letter" in text(row)
