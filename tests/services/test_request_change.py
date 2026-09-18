"""``document.request``: a letter's demands, as one card.

The validation gate ST-08 names: drop the renewal request in, one card
lists the deadline and the line items it read, approving files the
request with its items, rejecting leaves nothing behind.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.service import DocumentService
from app.services.matters.matters import MatterService
from app.services.matters.requests import RequestService

ASKED = "Proof of GROSS monthly income for each pension."


async def _letter(db: AsyncSession) -> tuple[int, int]:
    matter = await MatterService(db).open(title="Medicaid renewal", reference="MA-RD-1")
    document = await DocumentService(db).ingest(
        b"%PDF-1.4 renewal", title="Bedner J Request.pdf", kind="letter"
    )
    await db.flush()
    return int(matter.id), int(document.id)


def _payload(matter_id: int, document_id: int, **over: object):
    from app.services.documents.domains.reading import ReadAsk, RequestPayload

    base: dict[str, object] = {
        "document_id": document_id,
        "matter_id": matter_id,
        "received_on": date(2026, 8, 24),
        "due_on": date(2026, 9, 8),
        "items": [
            ReadAsk(asked=ASKED, kind="figure", page=2, quote="Proof of GROSS monthly"),
        ],
    }
    return RequestPayload(**(base | over))


class TestTheCardListsWhatWasAsked:
    @pytest.mark.asyncio
    async def test_it_names_the_matter_the_deadline_and_every_ask(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import request_describe

        matter_id, document_id = await _letter(async_db_session)
        rows = await request_describe(
            async_db_session, _payload(matter_id, document_id), None
        )
        said = {r.label: r.value for r in rows}

        assert said["Matter"] == "Medicaid renewal"
        assert said["Read from"] == "Bedner J Request.pdf"
        assert said["Due"] == "2026-09-08"
        assert "Proof of GROSS monthly income" in said["Record a figure"]
        assert "page 2" in said["Record a figure"]


class TestTheCardAdmitsWhatItMissed:
    @pytest.mark.asyncio
    async def test_a_dropped_demand_is_said_out_loud(
        self, async_db_session: AsyncSession
    ) -> None:
        """Five asks where the letter made six is a card somebody trusts
        as complete."""
        from app.services.documents.domains.reading import request_describe

        matter_id, document_id = await _letter(async_db_session)
        rows = await request_describe(
            async_db_session, _payload(matter_id, document_id, dropped=2), None
        )
        said = {r.label: r.value for r in rows}

        assert "2" in said["Not read"]
        assert "letter" in said["Not read"].casefold()

    @pytest.mark.asyncio
    async def test_a_clean_reading_says_nothing_about_it(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import request_describe

        matter_id, document_id = await _letter(async_db_session)
        rows = await request_describe(
            async_db_session, _payload(matter_id, document_id), None
        )
        assert "Not read" not in {r.label for r in rows}


class TestApprovingFilesIt:
    @pytest.mark.asyncio
    async def test_the_request_lands_with_its_items_and_cites_the_letter(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import request_execute

        matter_id, document_id = await _letter(async_db_session)

        made = await request_execute(
            async_db_session, _payload(matter_id, document_id), None
        )
        await async_db_session.commit()

        requests = RequestService(async_db_session)
        filed = await requests.get(made["request_id"])
        assert (filed.matter_id, filed.document_id) == (matter_id, document_id)
        assert (filed.due_on, filed.received_on) == (
            date(2026, 9, 8),
            date(2026, 8, 24),
        )
        items = await requests.items(filed.id)
        assert [(i.asked, i.kind, i.status) for i in items] == [
            (ASKED, "figure", "needed")
        ]

    @pytest.mark.asyncio
    async def test_rejecting_leaves_nothing_behind(
        self, async_db_session: AsyncSession
    ) -> None:
        """Proposing is not writing: until somebody approves, the matter
        has no request on it."""
        from app.services.finance.domains.writes.queue import propose, reject

        matter_id, document_id = await _letter(async_db_session)
        row = await propose(
            async_db_session,
            "document.request",
            _payload(matter_id, document_id).model_dump(mode="json"),
        )
        await reject(async_db_session, row.id)
        await async_db_session.commit()

        assert await RequestService(async_db_session).for_matter(matter_id) == []


class TestWhatItRefuses:
    def test_a_card_with_no_asks(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _payload(1, 1, items=[])

    @pytest.mark.asyncio
    async def test_a_matter_nobody_opened(self, async_db_session: AsyncSession) -> None:
        from app.services.documents.domains.reading import request_execute

        _matter_id, document_id = await _letter(async_db_session)
        with pytest.raises(ValueError, match="matter"):
            await request_execute(
                async_db_session, _payload(999_999, document_id), None
            )

    @pytest.mark.asyncio
    async def test_a_document_nobody_filed(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import request_execute

        matter_id, _document_id = await _letter(async_db_session)
        with pytest.raises(ValueError, match="document"):
            await request_execute(async_db_session, _payload(matter_id, 999_999), None)

    def test_it_is_in_the_one_queue(self) -> None:
        from app.services.finance.domains.writes.registry import executor_for

        assert executor_for("document.request").title


class TestALetterOnAMatterProposesItsDemands:
    """The seam: a letter filed on a matter is read for what it asks,
    and the asks arrive as a card. A letter filed nowhere is not, because
    a request with no matter has nothing to be a request ON."""

    async def _pages(self, db: AsyncSession, document_id: int, *texts: str) -> None:
        from app.services.documents.models import DocumentPage

        for number, text in enumerate(texts, start=1):
            db.add(
                DocumentPage(
                    document_id=document_id,
                    page_number=number,
                    status="read",
                    method="text_layer",
                    text=text,
                )
            )
        await db.flush()

    async def _cards(self, db: AsyncSession, document_id: int) -> list:
        from app.services.finance.domains.writes.queue import list_changes

        return [
            c
            for c in await list_changes(db, status="pending")
            if c.change_type == "document.request"
            and c.payload["document_id"] == document_id
        ]

    def _reader(self):
        from app.services.documents.domains.reading.letters import (
            LetterReading,
            ReadItem,
        )

        async def read(pages):
            return LetterReading(
                received_on=date(2026, 8, 24),
                due_on=date(2026, 9, 8),
                items=[
                    ReadItem(
                        asked=ASKED, kind="figure", page=1, quote="Proof of GROSS"
                    ),
                    ReadItem(
                        asked="Invented", kind="document", page=4, quote="not here"
                    ),
                ],
            )

        return read

    @pytest.mark.asyncio
    async def test_filed_on_a_matter_the_asks_arrive_as_a_card(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading
        from app.services.documents.service import DocumentService
        from app.services.matters.models import matter_tag

        matter_id, document_id = await _letter(async_db_session)
        await self._pages(async_db_session, document_id, "Proof of GROSS monthly")
        await DocumentService(async_db_session).tag(document_id, matter_tag(matter_id))
        await async_db_session.flush()

        await propose_reading(async_db_session, document_id, read_letter=self._reader())
        await async_db_session.commit()

        (card,) = await self._cards(async_db_session, document_id)
        assert card.payload["matter_id"] == matter_id
        assert card.payload["due_on"] == "2026-09-08"
        # The ask citing a page the letter does not have never reached it,
        # and the card admits that one was lost on the way.
        assert [i["asked"] for i in card.payload["items"]] == [ASKED]
        assert card.payload["dropped"] == 1

    @pytest.mark.asyncio
    async def test_filed_nowhere_it_asks_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        _matter_id, document_id = await _letter(async_db_session)
        await self._pages(async_db_session, document_id, "Proof of GROSS monthly")

        await propose_reading(async_db_session, document_id, read_letter=self._reader())
        await async_db_session.commit()

        assert await self._cards(async_db_session, document_id) == []

    @pytest.mark.asyncio
    async def test_with_no_model_reachable_the_paper_still_reads(
        self, async_db_session: AsyncSession
    ) -> None:
        """A stack without the AI service files its metadata as before;
        only the demands go unread."""
        from app.services.documents.domains.reading import propose_reading
        from app.services.documents.service import DocumentService
        from app.services.finance.domains.writes.queue import list_changes
        from app.services.matters.models import matter_tag

        matter_id, document_id = await _letter(async_db_session)
        await self._pages(
            async_db_session, document_id, "Dear Mr. Bedner:\nAs of 08/24/2026"
        )
        await DocumentService(async_db_session).tag(document_id, matter_tag(matter_id))
        await async_db_session.flush()

        await propose_reading(async_db_session, document_id, read_letter=None)
        await async_db_session.commit()

        assert await self._cards(async_db_session, document_id) == []
        assert [
            c.change_type
            for c in await list_changes(async_db_session, status="pending")
            if c.payload.get("document_id") == document_id
        ] == ["document.metadata"]

    @pytest.mark.asyncio
    async def test_reading_it_again_does_not_stack_up_cards(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading
        from app.services.documents.service import DocumentService
        from app.services.matters.models import matter_tag

        matter_id, document_id = await _letter(async_db_session)
        await self._pages(async_db_session, document_id, "Proof of GROSS monthly")
        await DocumentService(async_db_session).tag(document_id, matter_tag(matter_id))
        await async_db_session.flush()

        for _ in range(2):
            await propose_reading(
                async_db_session, document_id, read_letter=self._reader()
            )
            await async_db_session.commit()

        assert len(await self._cards(async_db_session, document_id)) == 1

    @pytest.mark.asyncio
    async def test_a_letter_whose_demands_are_already_filed_asks_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        """Somebody typed the asks in before the reader got to it. A
        second request on the same letter is a duplicate matter, not a
        second reading."""
        from app.services.documents.domains.reading import propose_reading
        from app.services.documents.service import DocumentService
        from app.services.matters.models import matter_tag

        matter_id, document_id = await _letter(async_db_session)
        await self._pages(async_db_session, document_id, "Proof of GROSS monthly")
        await DocumentService(async_db_session).tag(document_id, matter_tag(matter_id))
        await RequestService(async_db_session).record(
            matter_id=matter_id,
            document_id=document_id,
            items=[{"asked": ASKED}],
        )
        await async_db_session.flush()

        await propose_reading(async_db_session, document_id, read_letter=self._reader())
        await async_db_session.commit()

        assert await self._cards(async_db_session, document_id) == []
