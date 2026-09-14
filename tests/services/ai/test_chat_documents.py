"""A PDF attached in chat is READ, not shipped.

The picker refused anything but images, so a statement had to be
screenshotted a page at a time - which is what happened with a pool
loan: two screenshots to get a balance, a rate and a payment that were
all on one PDF.

The reading is the documents service's, not a second copy of it: its
text layer per page, a vision model over the pages that have none. What
this module adds is the join - the bytes reach the store, the extracted
text reaches the turn as a marker, and the agent asks for it with the
same ``pasted`` tool a pasted page uses, because from the conversation's
side they are the same thing.
"""

import base64

import pytest

from app.services.ai.domains.chat.attachments import ChatAttachment, prepare_turn
from app.services.ai.domains.chat.pastes import pasted
from app.services.ai.domains.chat.user_memory import memory_user
from tests._pdf import pdf_bytes

STATEMENT = ["Account 5500\nBalance $9,873.66", "Interest rate 7.99%"]


def _attached(
    texts: list[str], name: str = "statement.pdf", unique: str = ""
) -> ChatAttachment:
    """One attached PDF.

    ``unique`` makes the BYTES differ between tests. Documents are
    content-addressed, so two tests attaching identical files share one
    document row while each gets its own object store - the second finds
    the row, its bytes are somewhere else, and extraction reads nothing.
    A test that needs its own document has to bring its own content.
    """
    body = [*texts, unique] if unique else texts
    return ChatAttachment(
        media_type="application/pdf",
        data_b64=base64.b64encode(pdf_bytes(body)).decode(),
        name=name,
    )


class TestThePdfReachesTheTurn:
    @pytest.mark.asyncio
    async def test_the_message_carries_a_marker_not_the_pages(self) -> None:
        text, metadata = await prepare_turn(
            "what does this say?", [_attached(STATEMENT, unique="one")], "doc-u1"
        )

        assert text.startswith("what does this say?")
        assert "pasted text #" in text
        assert "9,873.66" not in text
        assert len(metadata["pastes"]) == 1

    @pytest.mark.asyncio
    async def test_the_agent_reads_the_pages_back(self) -> None:
        _, metadata = await prepare_turn("read this", [_attached(STATEMENT, unique="two")], "doc-u2")
        paste_id = metadata["pastes"][0]["id"]

        with memory_user("doc-u2"):
            body = await pasted(paste_id)

        assert "Balance $9,873.66" in body
        assert "Interest rate 7.99%" in body
        # Pages are named: a figure's meaning often depends on which page
        # it came off, and statements repeat their headings.
        assert "--- page 1 ---" in body
        assert "--- page 2 ---" in body

    @pytest.mark.asyncio
    async def test_the_text_has_one_home(self) -> None:
        """The entry points at the document; it does not copy the text
        into the paste store beside it."""
        _, metadata = await prepare_turn("read this", [_attached(STATEMENT, unique="three")], "doc-u3")

        entry = metadata["pastes"][0]
        assert entry["document_id"]
        assert "key" not in entry

    @pytest.mark.asyncio
    async def test_the_same_document_twice_is_one_entry(self) -> None:
        """The store is content-addressed, so the second attach finds the
        first document rather than making another."""
        from app.services.ai.domains.chat.user_memory import load_user_pastes

        same = _attached(STATEMENT, unique="four")
        await prepare_turn("once", [same], "doc-u4")
        await prepare_turn("twice", [same], "doc-u4")

        assert len(await load_user_pastes("doc-u4")) == 1


class TestWhatCannotBeRead:
    @pytest.mark.asyncio
    async def test_an_unreadable_document_says_so_in_the_message(self) -> None:
        """An attachment the user watched upload and then never hears
        about again reads as the app losing it."""
        junk = ChatAttachment(
            media_type="application/pdf",
            data_b64=base64.b64encode(b"not a pdf at all").decode(),
            name="broken.pdf",
        )

        text, metadata = await prepare_turn("read this", [junk], "doc-u5")

        assert "broken.pdf" in text
        assert "could not be processed" in text
        assert "pastes" not in metadata

    @pytest.mark.asyncio
    async def test_a_reader_that_could_not_run_is_not_called_a_scan(self) -> None:
        """The two cases are not the same advice. A scan is answered with
        a screenshot; a reader that could not RUN is answered by nobody -
        the text may be perfectly good. Live, the extraction library was
        missing from a running image and every statement came back
        reported as a scan, which sent the reader to screenshot PDFs that
        were already readable."""
        junk = ChatAttachment(
            media_type="application/pdf",
            data_b64=base64.b64encode(b"not a pdf at all").decode(),
            name="broken.pdf",
        )

        text, _ = await prepare_turn("read this", [junk], "doc-u7")

        assert "screenshot" not in text


class TestImagesAreUntouched:
    @pytest.mark.asyncio
    async def test_an_image_still_rides_as_bytes(self) -> None:
        """Only documents are read here; a screenshot is still a
        screenshot, and the vision path is unchanged."""
        image = ChatAttachment(
            media_type="image/png", data_b64=base64.b64encode(b"x").decode(), name="s.png"
        )

        text, metadata = await prepare_turn("look", [image], "doc-u6")

        assert "attached 1 image" in text
        assert "pasted text #" not in text
        assert metadata["attachments"][0]["media_type"] == "image/png"


class TestSheKnowsToReadIt:
    def test_the_prompt_says_a_pdf_arrives_as_a_marker(self) -> None:
        """Otherwise the likely answer is one built from the filename."""
        from app.services.finance.domains.detection.analyst.prompts import (
            FINANCE_CHAT_SYSTEM_PROMPT,
        )

        assert "An attached PDF has already been READ" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "before answering from the filename" in FINANCE_CHAT_SYSTEM_PROMPT


class TestAScheduleIsItsOwnKind:
    """An amortization schedule is not a statement and not a letter: it
    is the lender's own projection of every future payment. Broad on
    purpose - a payment plan, a delivery schedule and an appointment
    schedule are the same kind of paper, and a kind narrow enough to name
    one lender's document is a kind nobody else can file under."""

    def test_schedule_is_a_document_kind(self) -> None:
        from app.services.documents.models import DOCUMENT_KINDS

        assert "schedule" in DOCUMENT_KINDS

    def test_the_constraint_is_derived_from_the_list(self) -> None:
        """The kinds lived twice - the tuple and the same strings typed
        into the CHECK - and two copies of one truth is how a kind ends
        up legal in Python and rejected by the database."""
        from app.services.documents.models import DOCUMENT_KINDS, kind_check

        clause = kind_check()
        assert all(f"'{kind}'" in clause for kind in DOCUMENT_KINDS)
        assert clause.count(",") == len(DOCUMENT_KINDS) - 1

    @pytest.mark.asyncio
    async def test_a_document_can_be_stored_as_one(self) -> None:
        """The kinds are a CHECK constraint, so a kind the database has
        not been told about is a write that fails, not a value that
        quietly sits there."""
        from app.core.db import get_async_session
        from app.services.documents.service import DocumentService

        async with get_async_session() as session:
            document = await DocumentService(session).ingest(
                b"%PDF-1.4 schedule", title="Amortization.pdf", kind="schedule"
            )
            await session.commit()
            assert document.kind == "schedule"
