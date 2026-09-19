"""A card that cites a page SHOWS that page.

The lesson the real data kept teaching: a citation proves where text
came from, not that the reading was right. An approval card that names
"page 2" asks somebody to trust a reading they cannot see, and the one
moment they could check it is the moment they are deciding (2026-09-18).

So a row that cites a page carries the page WITH it, and the card draws
it beside the claim.
"""

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession


async def _paper(db: AsyncSession) -> Any:
    from app.services.documents.models import Document, DocumentPage

    document = Document(
        title="NYSLRS Monthly Statement",
        storage_key="testcase/nyslrs.pdf",
        media_type="application/pdf",
        content_hash="testcase-hash",
        size_bytes=17,
    )
    db.add(document)
    await db.flush()
    db.add(
        DocumentPage(
            document_id=document.id,
            page_number=2,
            status="read",
            method="text",
            text="Monthly Pension Benefit: $2,178.94",
            image_key="testcase/nyslrs-2.png",
        )
    )
    await db.flush()
    return document


class TestTheCardShowsWhatItCites:
    @pytest.mark.asyncio
    async def test_an_evidence_link_carries_its_page(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            EvidencePayload,
            evidence_describe,
        )
        from app.services.matters.matters import MatterService
        from app.services.matters.requests import RequestService

        document = await _paper(async_db_session)
        matter = await MatterService(async_db_session).open(
            title="Medicaid renewal", reference="CP-1"
        )
        request = await RequestService(async_db_session).record(
            matter_id=matter.id,
            items=[{"asked": "Proof of gross monthly income"}],
        )
        items = await RequestService(async_db_session).items(request.id)

        rows = await evidence_describe(
            async_db_session,
            EvidencePayload(
                document_id=document.id,
                request_item_id=items[0].id,
                page=2,
                because="Monthly Pension Benefit: $2,178.94",
            ),
            None,
        )

        cited = [row for row in rows if row.page]
        assert [(row.document_id, row.page) for row in cited] == [(document.id, 2)]

    @pytest.mark.asyncio
    async def test_a_figure_read_off_a_document_carries_its_page(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import RecordFactPayload, record_fact_describe
        from app.services.matters.service import PartyService

        document = await _paper(async_db_session)
        james = await PartyService(async_db_session).create(
            name="James Testcase", kind="person"
        )
        await async_db_session.flush()

        rows = await record_fact_describe(
            async_db_session,
            RecordFactPayload(
                subject_party_id=james.id,
                attribute="gross_income",
                value_cents=217894,
                period="month",
                provenance="document",
                document_id=document.id,
                page=2,
                source_note="Monthly Pension Benefit",
            ),
            None,
        )

        cited = [row for row in rows if row.page]
        assert [(row.document_id, row.page) for row in cited] == [(document.id, 2)]

    @pytest.mark.asyncio
    async def test_the_card_naming_a_document_shows_its_front(
        self, async_db_session: AsyncSession
    ) -> None:
        """The card that renames paper is the one most worth seeing the
        paper on: a title read off a letterhead is checked by looking at
        the letterhead."""
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            metadata_describe,
        )

        document = await _paper(async_db_session)
        # Still named after the file it arrived as: a rename proposed
        # for a document somebody has already named is refused, and
        # rightly - see test_document_changes.
        document.title = "nyslrs-08-2026.pdf"
        async_db_session.add(document)
        await async_db_session.flush()

        rows = await metadata_describe(
            async_db_session,
            MetadataPayload(
                document_id=document.id,
                title={
                    "value": "NYSLRS statement, August 2026",
                    "page": 2,
                    "because": "Monthly Pension Benefit: $2,178.94",
                },
            ),
            None,
        )

        cited = [row for row in rows if row.page]
        assert [(row.document_id, row.page) for row in cited] == [(document.id, 2)]

    def test_a_row_about_nothing_on_paper_cites_no_page(self) -> None:
        from app.services.finance.schemas import ChangeDisplayRow

        row = ChangeDisplayRow(label="Contact", value="Hudson Valley Credit Union")
        assert row.document_id is None and row.page is None
