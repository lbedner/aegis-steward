"""The Mail section: what came in, and what each message became (MI-06).

Both render paths; selectors. The rows are the same ``arrivals`` the
assistant reads, so the page asserts the row's own ``became`` rather
than restating the sentence.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlmodel import delete

from app.core.clock import utcnow
from app.core.db import get_async_session
from app.services.mail import ingest
from app.services.mail.arrivals import arrivals
from app.services.mail.models import MailAttachment, MailBatch, MailMessage
from app.services.matters.service import PartyService
from tests._mbox import PDF, mbox_bytes, message
from tests.web.dom import none, one, table_rows, text
from tests.web.test_mail_import import no_reader  # noqa: F401

# An address only this module uses. The app-owned database lives for the
# whole run and other modules file "Delta Dental" under ``_mbox``'s
# default address, so a shared one matches THEIR party and the From cell
# links somewhere else - under xdist, depending on who ran first.
KNOWN = "claims@mail-section.example"


@pytest.fixture
async def bare() -> None:
    """No mail on the shelf. The app-owned database lives for the whole
    run, so what an earlier module's import filed is still here."""
    async with get_async_session() as db:
        for table in (MailAttachment, MailMessage, MailBatch):
            await db.exec(delete(table))  # type: ignore[call-overload]
        await db.commit()


@pytest.fixture
async def shelf(bare: None, no_reader: list[int]) -> int:  # noqa: F811
    """A known sender with an attachment, a stranger's letter, and an
    empty newsletter - the milestone's three. Seeded through the app-owned
    engine, which is what the page reads."""
    async with get_async_session() as db:
        delta = await PartyService(db).create(
            name="Delta Dental", kind="organization", contact={"email": KNOWN}
        )
        await db.commit()
        party_id = int(delta.id)
    data = mbox_bytes(
        [
            message(
                sender=f"Delta Dental <{KNOWN}>",
                subject="Your statement",
                attachments=[("statement.pdf", "application/pdf", PDF)],
            ),
            message(
                sender="County <intake@mail-section.example>", subject="Renewal notice"
            ),
            message(
                sender="News <news@mail-section.example>",
                subject="Weekly deals",
                body="",
            ),
        ]
    )
    await ingest.ingest_mail(get_async_session, data=data, file_name="takeout.mbox")
    return party_id


async def _rows_by_subject() -> dict[str, Any]:
    async with get_async_session() as db:
        [day] = await arrivals(db, owner_user_id=None)
    return {row.subject: row for row in day.messages}


class TestWhatCameIn:
    async def test_today_holds_the_three(self, client: TestClient, shelf: int) -> None:
        page = client.get("/mail").text
        today = one(page, f"#day-{utcnow().date().isoformat()}")
        assert {text(r["Subject"]) for r in table_rows(today)} == {
            "Your statement",
            "Renewal notice",
            "Weekly deals",
        }

    async def test_a_known_sender_is_its_contact(
        self, client: TestClient, shelf: int
    ) -> None:
        page = client.get("/mail").text
        row = next(
            r for r in table_rows(page) if text(r["Subject"]) == "Your statement"
        )
        link = one(row["From"], f'a[href="/contacts/{shelf}"]')
        assert text(link) == "Delta Dental"

    async def test_the_subject_opens_the_letter(
        self, client: TestClient, shelf: int
    ) -> None:
        arrived = (await _rows_by_subject())["Your statement"]
        page = client.get("/mail").text
        row = next(
            r for r in table_rows(page) if text(r["Subject"]) == "Your statement"
        )
        door = one(row["Subject"], "[data-open]")
        assert door.get("hx-get") == f"/documents/{arrived.letter_id}"
        paper = one(row["Attachments"], "[data-open]")
        assert paper.get("hx-get") == f"/documents/{arrived.attachments[0].document_id}"

    async def test_became_is_the_rows_own_sentence(
        self, client: TestClient, shelf: int
    ) -> None:
        arrived = await _rows_by_subject()
        page = client.get("/mail").text
        for row in table_rows(page):
            assert text(row["Became"]) == arrived[text(row["Subject"])].became


class TestTheShell:
    def test_fragment_has_no_shell(self, hx: TestClient) -> None:
        none(hx.get("/mail").text, "html")

    def test_an_empty_shelf_says_so(self, client: TestClient, bare: None) -> None:
        page = client.get("/mail").text
        one(page, "[data-empty] h2")
        none(page, "table")

    def test_the_page_opens_the_upload_door(self, hx: TestClient) -> None:
        one(hx.get("/mail").text, 'button[hx-get="/documents/new"]')

    def test_the_sidebar_links_here(self, client: TestClient) -> None:
        one(client.get("/mail").text, 'aside#sidebar nav a[href="/mail"]')
