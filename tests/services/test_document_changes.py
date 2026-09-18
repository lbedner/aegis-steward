"""``document.metadata``: what a document says it is, as a card.

An LLM or a regex silently mis-reading the execution date on a legal
document is precisely the failure the approval queue exists to prevent,
so a document's OWN metadata goes through the queue like everything
else. Every field on the card names the page and the line it was read
from; a field that cannot cite one is not proposed.
"""

from datetime import date
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.service import DocumentService


async def _filed(db: AsyncSession, title: str = "Mortgage Interest Statement.pdf"):
    return await DocumentService(db).ingest(
        b"%PDF-1.4 " + title.encode(), title=title, media_type="application/pdf"
    )


class TestTheCardShowsItsWorking:
    @pytest.mark.asyncio
    async def test_each_field_names_the_page_and_the_line(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_describe,
        )

        document = await _filed(async_db_session)
        payload = MetadataPayload(
            document_id=int(document.id),
            kind=ReadValue(
                value="statement", page=1, because="Mortgage Interest Statement"
            ),
            document_date=ReadValue(
                value="2026-03-03", page=1, because="Statement Date: March 3, 2026"
            ),
        )

        said = {
            r.label: r.value
            for r in await metadata_describe(async_db_session, payload, None)
        }
        assert said["Document"] == "Mortgage Interest Statement.pdf"
        assert said["Kind"].startswith("other → statement")
        assert said["Dated"].startswith("- → 2026-03-03")
        assert (
            "page 1" in said["Kind"] and "Mortgage Interest Statement" in said["Kind"]
        )

    @pytest.mark.asyncio
    async def test_approving_it_files_what_it_read(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )

        document = await _filed(async_db_session)
        await metadata_execute(
            async_db_session,
            MetadataPayload(
                document_id=int(document.id),
                kind=ReadValue(value="statement", page=1, because="Statement"),
                document_date=ReadValue(value="2026-03-03", page=1, because="As of"),
            ),
            None,
        )
        await async_db_session.commit()

        filed = await DocumentService(async_db_session).get(int(document.id))
        assert (filed.kind, filed.document_date) == ("statement", date(2026, 3, 3))
        # The bytes and the title are untouched: a document is what arrived.
        assert filed.title == "Mortgage Interest Statement.pdf"


class TestWhatItRefuses:
    def test_a_kind_the_shelf_does_not_allow(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload, ReadValue

        with pytest.raises(ValidationError):
            MetadataPayload(
                document_id=1,
                kind=ReadValue(value="invoice", page=1, because="Invoice"),
            )

    def test_a_date_that_is_not_a_date(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload, ReadValue

        with pytest.raises(ValidationError):
            MetadataPayload(
                document_id=1,
                document_date=ReadValue(value="the third", page=1, because="x"),
            )

    def test_a_card_that_would_change_nothing(self) -> None:
        from pydantic import ValidationError

        from app.services.documents.domains.reading import MetadataPayload

        with pytest.raises(ValidationError):
            MetadataPayload(document_id=1)

    @pytest.mark.asyncio
    async def test_a_document_nobody_filed(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import (
            MetadataPayload,
            ReadValue,
            metadata_execute,
        )

        with pytest.raises(ValueError, match="document"):
            await metadata_execute(
                async_db_session,
                MetadataPayload(
                    document_id=999_999,
                    kind=ReadValue(value="letter", page=1, because="Dear"),
                ),
                None,
            )


class TestItIsInTheOneQueue:
    def test_the_change_type_is_registered(self) -> None:
        from app.services.finance.domains.writes.registry import executor_for

        assert executor_for("document.metadata").title


class TestReadingADocumentProducesACard:
    """The whole point of ST-08: dropping paper in produces a card,
    without anybody opening the chat and asking for one."""

    async def _read(self, db: AsyncSession, title: str, *texts: str) -> int:
        from app.services.documents.models import DocumentPage

        document = await _filed(db, title)
        for number, text in enumerate(texts, start=1):
            db.add(
                DocumentPage(
                    document_id=int(document.id),
                    page_number=number,
                    status="read",
                    method="text_layer",
                    text=text,
                )
            )
        await db.flush()
        return int(document.id)

    async def _cards(self, db: AsyncSession, document_id: int) -> list[Any]:
        from app.services.finance.domains.writes.queue import list_changes

        return [
            c
            for c in await list_changes(db, status="pending")
            if c.change_type == "document.metadata"
            and c.payload["document_id"] == document_id
        ]

    @pytest.mark.asyncio
    async def test_what_it_read_lands_as_one_pending_card(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session,
            "Aug-2026.pdf",
            "CITIZENS BANK\nMortgage Interest Statement\nStatement Date: March 3, 2026\n",
        )

        assert await propose_reading(async_db_session, document_id) is not None
        await async_db_session.commit()

        (card,) = await self._cards(async_db_session, document_id)
        assert card.payload["kind"]["value"] == "statement"
        assert card.payload["kind"]["page"] == 1
        assert card.payload["document_date"]["value"] == "2026-03-03"
        # Nothing has moved yet: the document is untouched until approval.
        filed = await DocumentService(async_db_session).get(document_id)
        assert filed.kind == "other" and filed.document_date is None

    @pytest.mark.asyncio
    async def test_reading_it_again_does_not_stack_up_cards(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session, "Twice.pdf", "Statement\nAs of 09/16/2026\n"
        )
        await propose_reading(async_db_session, document_id)
        await async_db_session.commit()
        assert await propose_reading(async_db_session, document_id) is None
        await async_db_session.commit()

        assert len(await self._cards(async_db_session, document_id)) == 1

    @pytest.mark.asyncio
    async def test_it_proposes_nothing_it_already_says(
        self, async_db_session: AsyncSession
    ) -> None:
        """A card that changes nothing wastes a decision."""
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session, "Known.pdf", "Statement\nAs of 09/16/2026\n"
        )
        await DocumentService(async_db_session).update(
            document_id, {"kind": "statement", "document_date": date(2026, 9, 16)}
        )
        await async_db_session.flush()

        assert await propose_reading(async_db_session, document_id) is None
        assert await self._cards(async_db_session, document_id) == []

    @pytest.mark.asyncio
    async def test_a_page_that_says_nothing_certain_proposes_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading import propose_reading

        document_id = await self._read(
            async_db_session, "Quiet.pdf", "Page 1 of 1\nThank you for your business.\n"
        )

        assert await propose_reading(async_db_session, document_id) is None
        assert await self._cards(async_db_session, document_id) == []
