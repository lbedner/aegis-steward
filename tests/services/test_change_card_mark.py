"""A card about paper wears the paper's mark.

Every other card carries one: a proposal about a transaction wears its
payee's logo, the same face the register gives that row. A card about a
DOCUMENT wore the first letter of its filename - "2026-08-17.pdf" came
out a grey "2", which is not a mark, it is the absence of one where
every neighbour has one (2026-09-19).

The shelf already draws the answer: the file's own type mark.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession


async def _document(db: AsyncSession, title: str, media_type: str | None):
    from app.services.documents.models import Document

    document = Document(
        title=title,
        storage_key=f"testcase/{title}",
        media_type=media_type,
        content_hash=f"testcase-mark-{title}",
        size_bytes=9,
    )
    db.add(document)
    await db.flush()
    return document


class TestWhatACardAboutPaperWears:
    @pytest.mark.asyncio
    async def test_the_file_mark_and_the_papers_name(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.components.backend.api.finance.changes import document_marks

        document = await _document(
            async_db_session, "2026-08-17.pdf", "application/pdf"
        )

        marks = await document_marks(async_db_session, [document.id])

        assert marks[document.id]["payee"] == "2026-08-17.pdf"
        assert marks[document.id]["icon_url"].endswith("/pdf.svg")

    @pytest.mark.asyncio
    async def test_a_format_nobody_knows_gets_the_plain_sheet(
        self, async_db_session: AsyncSession
    ) -> None:
        """A wrong mark is worse than a neutral one: it is read as a
        fact about the file."""
        from app.components.backend.api.finance.changes import document_marks

        document = await _document(async_db_session, "mystery.xyz", None)

        marks = await document_marks(async_db_session, [document.id])
        assert marks[document.id]["icon_url"].endswith(".svg")
        assert not marks[document.id]["icon_url"].endswith("/pdf.svg")

    @pytest.mark.asyncio
    async def test_a_document_that_is_gone_marks_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.components.backend.api.finance.changes import document_marks

        assert await document_marks(async_db_session, [999999]) == {}

    @pytest.mark.asyncio
    async def test_nothing_asked_for_is_no_query_at_all(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.components.backend.api.finance.changes import document_marks

        assert await document_marks(async_db_session, []) == {}
