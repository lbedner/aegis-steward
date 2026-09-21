"""A file of mail goes through the same door as any document, and is
followed while it is read (pattern 5). Both render paths; selectors.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from tests._mbox import PDF, eml_bytes, mbox_bytes, message
from tests.web.conftest import await_job
from tests.web.dom import one, select, stat, text
from tests.web.test_import import frames_of

MBOX = (
    "export.mbox",
    mbox_bytes([message(attachments=[("statement.pdf", "application/pdf", PDF)])]),
    "application/mbox",
)


# No per-test session fixture for the job, on purpose. The documents
# routes open their own sessions (the app-owned engine), so the job must
# write there too or the shelf reads a different database - handoff trap
# 3.1. The autouse ``_no_production_database`` already points app-owned
# sessions at the test's file-backed engine; the job reaches it by the
# real path.


@pytest.fixture
def no_reader(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The attachments' reads would go to the worker; record them instead."""
    from app.services.documents.domains.extraction import dispatch

    asked: list[int] = []

    async def _fake(document_id: int, *, owner_user_id: Any, force: bool) -> str:
        asked.append(document_id)
        return "job-read"

    monkeypatch.setattr(dispatch, "start_extraction", _fake)
    return asked


def _upload(client: TestClient, upload: tuple) -> Any:
    return client.post("/documents/new", files={"file": upload}, data={"kind": "other"})


class TestTheDoor:
    def test_the_dialog_accepts_mail(self, hx: TestClient) -> None:
        picker = one(hx.get("/documents/new").text, 'input[type="file"][name="file"]')
        accept = picker.get("accept") or ""
        assert ".mbox" in accept and ".eml" in accept

    def test_an_mbox_is_followed_not_filed(
        self, client: TestClient, no_reader: list[int]
    ) -> None:
        """Any other file is filed and the dialog closes. A mailbox is a
        job, and the answer is the follower - pattern 5, as the finance
        import answers."""
        answer = _upload(client, MBOX)
        assert answer.status_code == 200, answer.text
        follower = one(answer.text, "[sse-connect]")
        assert follower.get("hx-ext") == "sse" and follower.get("sse-swap") == "status"

    def test_a_single_message_is_mail_too(
        self, client: TestClient, no_reader: list[int]
    ) -> None:
        eml = ("claim.eml", eml_bytes(message()), "message/rfc822")
        one(_upload(client, eml).text, "[sse-connect]")


class TestFollowedToTheEnd:
    def test_the_frame_counts_what_landed_and_the_paper_is_on_the_shelf(
        self, live_client: TestClient, no_reader: list[int]
    ) -> None:
        answer = _upload(live_client, MBOX)
        stream_url = one(answer.text, "[sse-connect]").get("sse-connect") or ""
        job_id = stream_url.removeprefix("/jobs/").removesuffix("/events")

        assert await_job(live_client, job_id)["status"] == "done"
        final = frames_of(live_client, stream_url)[-1]
        assert stat(final, "Messages") == "1"
        assert stat(final, "Letters filed") == "1"
        assert stat(final, "Attachments filed") == "1"
        done = one(final, 'button[hx-get="/documents"]')
        assert done.get("hx-target") == "#app-content"

        shelf = live_client.get("/documents", params={"q": "statement"}).text
        rows = select(shelf, "#documents tbody tr")
        assert any("statement.pdf" in text(r) for r in rows), (
            f"searched rows: {[text(r) for r in rows]}; "
            f"unsearched: {[text(r) for r in select(live_client.get('/documents').text, '#documents tbody tr')][:8]}"
        )
        # And the shelf was asked to read both: the letter and its statement.
        assert len(no_reader) == 2

    def test_the_letter_opens_as_its_own_text(
        self, live_client: TestClient, no_reader: list[int]
    ) -> None:
        """A message filed as a letter IS text: the page text is the
        original, not a reading of it, so the dialog shows it where a PDF
        would go - not 'No preview' with the words folded away below."""
        answer = _upload(live_client, MBOX)
        stream_url = one(answer.text, "[sse-connect]").get("sse-connect") or ""
        assert (
            await_job(
                live_client, stream_url.removeprefix("/jobs/").removesuffix("/events")
            )["status"]
            == "done"
        )

        shelf = live_client.get(
            "/documents", params={"q": "claim has been processed"}
        ).text
        row = next(
            r
            for r in select(shelf, "#documents tbody tr")
            if "claim has been processed" in text(r)
        )
        dialog = live_client.get(f"/documents/{row.get('id').split('-')[-1]}").text
        original = one(dialog, "[data-original]")
        assert original.tag == "pre"
        assert "Your claim was processed." in text(original)


class TestTheFrameOnItsOwn:
    def test_done_is_told_apart_from_an_import_and_from_a_read(self) -> None:
        """``status.html`` renders a terminal frame per kind of job; the
        import's used to serve every job and took a finished READ to
        Accounts (trap 3.2). A mail import goes back to the shelf."""
        from app.components.web_frontend.routes.jobs import render_snapshot

        html = render_snapshot(
            None,  # type: ignore[arg-type]
            {
                "status": "done",
                "name": "mail-import:export.mbox",
                "label": "x",
                "result": {
                    "messages_total": 12,
                    "messages_new": 10,
                    "messages_duplicate": 2,
                    "letters_filed": 4,
                    "attachments_filed": 3,
                    "attachments_duplicate": 1,
                },
            },
        )
        assert stat(html, "Messages") == "12"
        assert stat(html, "Letters filed") == "4"
        assert stat(html, "Attachments filed") == "3"
        assert stat(html, "Already had") == "1"
        one(html, 'button[hx-get="/documents"]')
