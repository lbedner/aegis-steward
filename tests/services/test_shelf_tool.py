"""Finding a document without knowing its number.

``paper(document_id)`` needs an id you already have, and ``parties()``
only reports documents that are ALREADY tagged. So the documents most
in need of attention - the untagged ones, whose sender nobody has
recorded - were the only ones she could not see (found 2026-09-18,
when the letter carrying Dutchess County DSS's address could be read
only because somebody read its id out of the database by hand).
"""

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession


async def _doc(db: AsyncSession, title: str, kind: str = "other") -> Any:
    from app.services.documents.models import Document

    document = Document(
        title=title, kind=kind, storage_key=title, content_hash=title
    )
    db.add(document)
    await db.flush()
    return document


class TestTheShelf:
    @pytest.mark.asyncio
    async def test_it_finds_a_document_by_what_it_is_called(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.shelf import shelf

        await _doc(async_db_session, "Bedner J Request.pdf")
        await _doc(async_db_session, "NYSLRS Monthly Statement.pdf")

        found = await shelf(async_db_session, q="request")
        assert [d["title"] for d in found] == ["Bedner J Request.pdf"]

    @pytest.mark.asyncio
    async def test_the_search_does_not_care_about_case(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.shelf import shelf

        await _doc(async_db_session, "Bedner J Request.pdf")
        assert await shelf(async_db_session, q="BEDNER")

    @pytest.mark.asyncio
    async def test_it_can_show_only_what_nobody_has_attributed(
        self, async_db_session: AsyncSession
    ) -> None:
        """The whole reason this exists. An untagged document is one
        whose sender nobody has recorded, and those were invisible."""
        from app.services.documents.domains.shelf import shelf
        from app.services.documents.models import DocumentTag
        from app.services.matters.models import party_tag

        tagged = await _doc(async_db_session, "Filed Already.pdf")
        await _doc(async_db_session, "Nobody Owns This.pdf")
        async_db_session.add(
            DocumentTag(document_id=tagged.id, label=party_tag(7))
        )
        await async_db_session.flush()

        titles = [d["title"] for d in await shelf(async_db_session, unattributed=True)]
        assert titles == ["Nobody Owns This.pdf"]

    @pytest.mark.asyncio
    async def test_a_row_says_who_it_is_from(
        self, async_db_session: AsyncSession
    ) -> None:
        """So she does not have to call parties() and join by hand."""
        from app.services.documents.domains.shelf import shelf
        from app.services.documents.models import DocumentTag
        from app.services.matters.models import party_tag
        from app.services.matters.service import PartyService

        sender = await PartyService(async_db_session).create(
            name="Dutchess Testcase", kind="organization"
        )
        document = await _doc(async_db_session, "Request.pdf", kind="letter")
        async_db_session.add(
            DocumentTag(document_id=document.id, label=party_tag(sender.id))
        )
        await async_db_session.flush()

        row = (await shelf(async_db_session, q="request"))[0]
        assert row["from"] == [{"party_id": sender.id, "name": "Dutchess Testcase"}]
        assert row["kind"] == "letter"
        assert row["id"] == document.id

    @pytest.mark.asyncio
    async def test_an_empty_shelf_is_an_empty_list(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.shelf import shelf

        assert await shelf(async_db_session, q="nothing here") == []
