"""Reading a file of mail into the shelf.

The messages are already parsed by then; this is what the pipeline does
with them. Every session is short - the parse and the extraction
dispatch both happen holding nothing, for the reason #210 established.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.models import Document
from app.services.mail import ingest
from app.services.mail.models import MailAttachment, MailBatch, MailMessage
from app.services.matters.service import PartyService
from tests._mbox import PDF, eml_bytes, mbox_bytes, message


@pytest.fixture
def open_session(async_db_session: AsyncSession):
    """``ingest_mail`` takes a way to OPEN a database. Tests hand back the
    per-test session and never commit it."""

    @asynccontextmanager
    async def _open():
        yield async_db_session

    return _open


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every document the ingest asked to have read, in order."""
    asked: list[int] = []

    async def _fake(document_id: int, *, owner_user_id: Any, force: bool) -> str:
        asked.append(document_id)
        return f"job-{document_id}"

    # ``read_quietly`` looks this up in dispatch at call time, so the real
    # guard runs and only the enqueue is faked.
    from app.services.documents.domains.extraction import dispatch

    monkeypatch.setattr(dispatch, "start_extraction", _fake)
    return asked


async def _rows(db: AsyncSession, model: Any) -> list[Any]:
    return list((await db.exec(select(model))).all())


class TestEveryMessageLandsOnce:
    @pytest.mark.asyncio
    async def test_messages_become_rows(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        data = mbox_bytes([message(subject="one"), message(subject="two")])
        result = await ingest.ingest_mail(
            open_session, data=data, file_name="export.mbox"
        )

        assert result.messages_total == 2 and result.messages_new == 2
        assert [m.subject for m in await _rows(async_db_session, MailMessage)] == [
            "one",
            "two",
        ]

    @pytest.mark.asyncio
    async def test_an_overlapping_export_adds_only_what_is_new(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        """Takeout is re-run; the new file holds most of the old one. Each
        message lands once, on Message-ID, whatever file it came in."""
        old = message(subject="old", message_id="<old@example.com>")
        await ingest.ingest_mail(
            open_session, data=mbox_bytes([old]), file_name="first.mbox"
        )

        new = message(subject="new", message_id="<new@example.com>")
        result = await ingest.ingest_mail(
            open_session, data=mbox_bytes([old, new]), file_name="second.mbox"
        )

        assert (result.messages_new, result.messages_duplicate) == (1, 1)
        assert len(await _rows(async_db_session, MailMessage)) == 2

    @pytest.mark.asyncio
    async def test_the_same_bytes_are_answered_with_the_batch_that_read_them(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        data = mbox_bytes([message()])
        first = await ingest.ingest_mail(open_session, data=data, file_name="a.mbox")
        again = await ingest.ingest_mail(open_session, data=data, file_name="b.mbox")

        assert again.batch_id == first.batch_id
        assert again.messages_new == 0
        assert len(await _rows(async_db_session, MailBatch)) == 1

    @pytest.mark.asyncio
    async def test_a_file_that_is_not_mail_writes_nothing(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        from app.services.mail.parse import NotMailError

        with pytest.raises(NotMailError):
            await ingest.ingest_mail(open_session, data=b"%PDF-1.4", file_name="x.pdf")
        assert await _rows(async_db_session, MailBatch) == []


class TestAnAttachmentBecomesPaper:
    @pytest.mark.asyncio
    async def test_it_lands_on_the_shelf_dated_and_marked_as_mail(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        sent = datetime(2026, 8, 27, 9, 5, tzinfo=UTC)
        data = eml_bytes(
            message(sent=sent, attachments=[("statement.pdf", "application/pdf", PDF)])
        )
        result = await ingest.ingest_mail(
            open_session, data=data, file_name="claim.eml"
        )

        [paper] = [
            d for d in await _rows(async_db_session, Document) if d.kind != "letter"
        ]
        assert paper.title == "statement.pdf"
        assert paper.source == "mail" and paper.channel == "email"
        # The letter's own date, not the day it was imported.
        assert paper.document_date == sent.date()
        assert result.attachments_filed == 1

        [link] = await _rows(async_db_session, MailAttachment)
        assert link.document_id == paper.id and link.filename == "statement.pdf"
        # And the shelf reads it, as it reads any upload - after the
        # letter the message itself became.
        assert paper.id in dispatched and len(dispatched) == 2

    @pytest.mark.asyncio
    async def test_the_same_paper_on_two_messages_is_one_document(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        """A statement forwarded twice is one statement. The shelf holds
        it once; both messages still point at it; it is read once."""
        twice = [
            message(subject="first", attachments=[("s.pdf", "application/pdf", PDF)]),
            message(
                subject="forwarded", attachments=[("s.pdf", "application/pdf", PDF)]
            ),
        ]
        result = await ingest.ingest_mail(
            open_session, data=mbox_bytes(twice), file_name="x.mbox"
        )

        papers = [
            d for d in await _rows(async_db_session, Document) if d.kind != "letter"
        ]
        assert [d.title for d in papers] == ["s.pdf"]
        assert len(await _rows(async_db_session, MailAttachment)) == 2
        assert (result.attachments_filed, result.attachments_duplicate) == (1, 1)
        # Read once: the two letters, and the one statement.
        assert dispatched.count(papers[0].id) == 1 and len(dispatched) == 3

    @pytest.mark.asyncio
    async def test_paper_already_on_the_shelf_is_not_read_again(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        from app.services.documents.service import DocumentService

        already = await DocumentService(async_db_session).ingest(PDF, title="s.pdf")
        data = eml_bytes(message(attachments=[("s.pdf", "application/pdf", PDF)]))
        result = await ingest.ingest_mail(open_session, data=data, file_name="x.eml")

        assert result.attachments_duplicate == 1
        [link] = await _rows(async_db_session, MailAttachment)
        assert link.document_id == already.id
        # The statement is not read again; only the new letter is.
        assert already.id not in dispatched and len(dispatched) == 1


class TestTheSenderIsKnown:
    @pytest.mark.asyncio
    async def test_an_address_on_file_names_the_contact(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        delta = await PartyService(async_db_session).create(
            name="Delta Dental",
            kind="organization",
            contact={"email": "Claims@DeltaDentalIns.com"},
        )
        data = eml_bytes(message(sender="Delta Dental <claims@deltadentalins.com>"))
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")

        [row] = await _rows(async_db_session, MailMessage)
        # Case is the sender's to choose and nobody's to match on.
        assert row.party_id == delta.id

    @pytest.mark.asyncio
    async def test_a_labelled_reach_line_is_an_address_too(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        """A county office prints its caseworker's address as readily as
        its main one; ``also`` lines carry those, and they count."""
        county = await PartyService(async_db_session).create(
            name="Dutchess County DSS",
            kind="organization",
            contact={"also": [{"label": "caseworker", "value": "jdoe@dutchessny.gov"}]},
        )
        data = eml_bytes(message(sender="J Doe <JDOE@dutchessny.gov>"))
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")

        [row] = await _rows(async_db_session, MailMessage)
        assert row.party_id == county.id

    @pytest.mark.asyncio
    async def test_a_stranger_is_kept_and_named_nobody(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        data = eml_bytes(message(sender="Someone <who@nowhere.example>"))
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")
        [row] = await _rows(async_db_session, MailMessage)
        assert row.party_id is None and row.from_address == "who@nowhere.example"


class TestItSaysWhatItIsDoing:
    @pytest.mark.asyncio
    async def test_progress_reaches_the_label(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        said: list[str] = []

        async def on_label(text: str) -> None:
            said.append(text)

        data = mbox_bytes([message(), message()])
        await ingest.ingest_mail(
            open_session, data=data, file_name="export.mbox", on_label=on_label
        )
        # Before the work, with a count, and the batch's own record agrees.
        assert any("2" in line for line in said), said
        [batch] = await _rows(async_db_session, MailBatch)
        assert batch.status == "done" and batch.finished_at is not None
        assert batch.messages_total == 2


class TestTheMessageIsALetter:
    @pytest.mark.asyncio
    async def test_the_body_becomes_a_letter_already_read(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        from app.services.documents.models import DocumentPage

        sent = datetime(2026, 8, 27, 9, 5, tzinfo=UTC)
        data = eml_bytes(
            message(
                sender="Delta Dental <Claims@DeltaDentalIns.com>",
                subject="Your claim has been processed",
                body="Your claim was paid. Call 800-555-0100 with questions.",
                sent=sent,
            )
        )
        result = await ingest.ingest_mail(
            open_session, data=data, file_name="claim.eml"
        )

        [letter] = await _rows(async_db_session, Document)
        assert (
            letter.kind == "letter" and letter.title == "Your claim has been processed"
        )
        assert letter.source == "mail" and letter.channel == "email"
        assert letter.document_date == sent.date()
        [page] = await _rows(async_db_session, DocumentPage)
        # Already read: the mail IS text. Extraction skips a read page and
        # goes straight to proposing, so the letter says who it is from
        # the same way a scanned one does.
        assert page.document_id == letter.id and page.status == "read"
        assert page.method == "mail"
        lines = (page.text or "").splitlines()
        # The header is the letterhead: the address sits on page 1 for the
        # identity rules, the subject and date beside it.
        assert lines[0] == "From: Delta Dental <claims@deltadentalins.com>"
        assert "Your claim was paid." in page.text
        [row] = await _rows(async_db_session, MailMessage)
        assert row.document_id == letter.id
        assert result.letters_filed == 1
        assert dispatched == [letter.id]

    @pytest.mark.asyncio
    async def test_an_empty_body_files_no_letter(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        data = eml_bytes(
            message(body="", attachments=[("s.pdf", "application/pdf", PDF)])
        )
        result = await ingest.ingest_mail(open_session, data=data, file_name="x.eml")
        assert result.letters_filed == 0
        assert [d.title for d in await _rows(async_db_session, Document)] == ["s.pdf"]

    @pytest.mark.asyncio
    async def test_the_batch_counts_letters(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        data = mbox_bytes([message(body="one"), message(body="two")])
        await ingest.ingest_mail(open_session, data=data, file_name="x.mbox")
        [batch] = await _rows(async_db_session, MailBatch)
        assert batch.letters_filed == 2


async def _cards(db: AsyncSession) -> list[Any]:
    from app.services.finance.models.changes import FinancePendingChange

    return list(
        (
            await db.exec(
                select(FinancePendingChange).where(
                    FinancePendingChange.change_type == "contact.create"
                )
            )
        ).all()
    )


class TestAStrangerIsOfferedAsAContact:
    """The letterhead rule refuses to GUESS a sender off a printed line -
    that is how a second address book starts. A From header is not a
    guess: the sender asserted a name and an address. So a stranger is
    offered as a card (contact.create) and never written - ST-08's rule -
    and approving it files the letter under them on the next read."""

    @pytest.mark.asyncio
    async def test_an_unknown_sender_becomes_a_card(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        data = eml_bytes(
            message(
                sender="Optum Financial <OF-Service@of.optum.com>",
                subject="Your HSA Statement is Now Available",
            )
        )
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")

        [card] = await _cards(async_db_session)
        assert card.status == "pending" and card.proposed_by_agent == "mail"
        assert card.payload["name"] == "Optum Financial"
        assert card.payload["kind"] == "organization"
        assert card.payload["email"] == "of-service@of.optum.com"
        assert "Your HSA Statement is Now Available" in card.payload["note"]

    @pytest.mark.asyncio
    async def test_a_sender_on_file_is_a_match_not_a_stranger(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        await PartyService(async_db_session).create(
            name="Delta Dental",
            kind="organization",
            contact={"email": "claims@deltadentalins.com"},
        )
        data = eml_bytes(message(sender="Delta Dental <claims@deltadentalins.com>"))
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")
        assert await _cards(async_db_session) == []

    @pytest.mark.asyncio
    async def test_one_card_per_stranger_however_often_they_wrote(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        twice = [
            message(sender="Someone <who@nowhere.example>", subject="first"),
            message(sender="Someone <who@nowhere.example>", subject="second"),
        ]
        await ingest.ingest_mail(
            open_session, data=mbox_bytes(twice), file_name="x.mbox"
        )
        assert len(await _cards(async_db_session)) == 1

    @pytest.mark.asyncio
    async def test_a_card_already_waiting_is_not_offered_again(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        """Two exports, weeks apart, same unknown sender, nobody has
        answered the first card yet."""
        first = eml_bytes(message(sender="Someone <who@nowhere.example>", subject="a"))
        await ingest.ingest_mail(open_session, data=first, file_name="a.eml")
        second = eml_bytes(message(sender="Someone <who@nowhere.example>", subject="b"))
        await ingest.ingest_mail(open_session, data=second, file_name="b.eml")
        assert len(await _cards(async_db_session)) == 1

    @pytest.mark.asyncio
    async def test_a_rejected_card_is_not_offered_again(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        """No means no. A sender whose card was rejected writes again
        next month; offering them again is asking a question that was
        answered."""
        first = eml_bytes(message(sender="Someone <who@nowhere.example>", subject="a"))
        await ingest.ingest_mail(open_session, data=first, file_name="a.eml")
        [card] = await _cards(async_db_session)
        card.status = "rejected"
        async_db_session.add(card)
        await async_db_session.commit()

        second = eml_bytes(message(sender="Someone <who@nowhere.example>", subject="b"))
        await ingest.ingest_mail(open_session, data=second, file_name="b.eml")
        assert len(await _cards(async_db_session)) == 1

    @pytest.mark.asyncio
    async def test_no_display_name_means_the_domain_is_the_name(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        """``From: of-service@of.optum.com`` - no name at all. The address
        is not a name anybody would file under; the domain's own label is
        the nearest thing to one, and the address stays on the card."""
        data = eml_bytes(message(sender="of-service@of.optum.com"))
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")
        [card] = await _cards(async_db_session)
        assert card.payload["name"] == "Optum"
        assert card.payload["email"] == "of-service@of.optum.com"


class TestPaperIsFiledUnderTheSender:
    """Matching the address names the sender; the paper should then sit
    with them, the way a letter approved off a letterhead does - the
    same party tag, so Contacts shows it and nothing else has to learn
    a second way of filing. A stranger's paper waits for the card, and
    approving the card adopts everything they already sent."""

    @pytest.mark.asyncio
    async def test_a_known_senders_letter_and_attachments_are_filed_under_them(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        from app.services.documents.service import DocumentService
        from app.services.matters.models.core import party_tag

        delta = await PartyService(async_db_session).create(
            name="Delta Dental",
            kind="organization",
            contact={"email": "claims@deltadentalins.com"},
        )
        data = eml_bytes(
            message(
                sender="Delta Dental <claims@deltadentalins.com>",
                attachments=[("statement.pdf", "application/pdf", PDF)],
            )
        )
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")

        docs = DocumentService(async_db_session)
        [row] = await _rows(async_db_session, MailMessage)
        [link] = await _rows(async_db_session, MailAttachment)
        assert await docs.tags_for(int(row.document_id)) == [party_tag(int(delta.id))]
        assert await docs.tags_for(link.document_id) == [party_tag(int(delta.id))]

    @pytest.mark.asyncio
    async def test_approving_a_strangers_card_adopts_what_they_sent(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        from app.services.documents.service import DocumentService
        from app.services.finance.domains.writes.queue import approve
        from app.services.matters.models.core import party_tag

        data = eml_bytes(
            message(
                sender="Optum Financial <of-service@of.optum.com>",
                attachments=[("statement.pdf", "application/pdf", PDF)],
            )
        )
        await ingest.ingest_mail(open_session, data=data, file_name="x.eml")
        [row] = await _rows(async_db_session, MailMessage)
        assert row.party_id is None
        [card] = await _cards(async_db_session)

        done = await approve(async_db_session, int(card.id))
        party_id = int(done.result["party_id"])

        await async_db_session.refresh(row)
        assert row.party_id == party_id
        docs = DocumentService(async_db_session)
        [link] = await _rows(async_db_session, MailAttachment)
        assert await docs.tags_for(int(row.document_id)) == [party_tag(party_id)]
        assert await docs.tags_for(link.document_id) == [party_tag(party_id)]
