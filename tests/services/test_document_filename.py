"""What a document was CALLED when it arrived, kept.

Renaming a document overwrote its title, and "enrollee-notices-flyer.pdf"
stopped existing anywhere: not in the row, not in the storage key (a
content hash), not in the metadata. Six months later somebody searching
for what the bank called the download finds nothing (2026-09-19).

The payee pattern, applied to paper: the raw descriptor a transaction
arrived with is never touched and the curated payee sits beside it. A
document keeps its filename and gains a title.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tests._pdf import pdf_bytes


class TestItKeepsWhatArrived:
    @pytest.mark.asyncio
    async def test_the_filename_is_recorded_on_the_way_in(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import DocumentService

        document = await DocumentService(async_db_session).ingest(
            pdf_bytes(["Anything"]),
            title="enrollee-notices-flyer.pdf",
            media_type="application/pdf",
        )
        assert document.filename == "enrollee-notices-flyer.pdf"
        assert document.title == "enrollee-notices-flyer.pdf"

    @pytest.mark.asyncio
    async def test_renaming_leaves_it_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import DocumentService

        service = DocumentService(async_db_session)
        document = await service.ingest(
            pdf_bytes(["Anything else"]),
            title="AP_DRTNY107_23548895152718.pdf",
            media_type="application/pdf",
        )
        await service.update(document.id, {"title": "Delta Dental Application"})
        await async_db_session.flush()

        assert document.title == "Delta Dental Application"
        assert document.filename == "AP_DRTNY107_23548895152718.pdf"


class TestWhatCountsAsUnnamed:
    """With the original kept, the question stops being a guess."""

    def test_a_title_that_is_still_the_filename(self) -> None:
        from app.services.documents.domains.reading.titles import still_unnamed

        assert still_unnamed("scan0001.pdf", "scan0001.pdf")

    def test_a_title_somebody_gave_it(self) -> None:
        assert_named = "Delta Dental Application"
        from app.services.documents.domains.reading.titles import still_unnamed

        assert not still_unnamed(assert_named, "AP_DRTNY107_23548895152718.pdf")

    def test_without_a_filename_it_falls_back_to_the_guess(self) -> None:
        """Rows that predate the column keep the heuristic: their
        original is gone, and a shelf of them must still be nameable."""
        from app.services.documents.domains.reading.titles import still_unnamed

        assert still_unnamed("scan0001.pdf", None)
        assert not still_unnamed("Dad's POA", None)
