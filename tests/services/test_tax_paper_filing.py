"""Filing a photographed form names it, and the card shows the photo (#430).

Eight photos of 2025 tax forms were filed to a matter as eight cards
titled IMG_6611.jpeg ... IMG_6618.jpeg with no picture on them, though
Illiana had read every form: the filing card could say only where a
document went, never what it was.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.filing import (
    FileDocumentPayload,
    file_document_describe,
    file_document_execute,
    file_document_scan,
)


async def _photo(db: AsyncSession) -> tuple[str, str, int]:
    from app.core.storage import get_storage
    from app.services.ai.domains.chat.pastes import store_image
    from app.services.matters.matters import MatterService

    matter = await MatterService(db).open(title="2025 Tax Return")
    key = await get_storage().put(b"\xff\xd8\xff a 1099-INT", content_type="image/jpeg")
    photo = await store_image("0", key, "image/jpeg", "IMG_6611.jpeg", db)
    return photo["id"], key, int(matter.id or 0)


def _named(paste_id: str, matter_id: int, **over: Any) -> FileDocumentPayload:
    return FileDocumentPayload(
        **{
            "paste_id": paste_id,
            "matter_id": matter_id,
            "title": "Citizens Bank 1099-INT, 2025",
            "kind": "tax",
            "form_type": "1099-INT",
            "tax_year": 2025,
            **over,
        }
    )


class TestTheCardSaysWhatItIs:
    def test_a_form_nobody_has_heard_of_is_refused_at_the_door(self) -> None:
        with pytest.raises(ValidationError, match="1099-XYZ"):
            _named("p", 1, form_type="1099-XYZ")
        with pytest.raises(ValidationError, match="kind"):
            _named("p", 1, kind="memo")

    def test_a_form_type_is_tax_paper(self) -> None:
        assert _named("p", 1, kind=None).kind == "tax"

    @pytest.mark.asyncio
    async def test_the_card_names_it_and_pictures_it(
        self, async_db_session: AsyncSession
    ) -> None:
        paste_id, key, matter_id = await _photo(async_db_session)
        payload = _named(paste_id, matter_id)

        rows = await file_document_describe(async_db_session, payload, None)

        document = rows[0]
        assert document.value == "IMG_6611.jpeg → Citizens Bank 1099-INT, 2025"
        assert document.scan is True
        assert {r.label: r.value for r in rows}["Is"] == "tax · 1099-INT · 2025"
        assert await file_document_scan(async_db_session, payload, None) == key

    @pytest.mark.asyncio
    async def test_approving_files_it_under_its_name(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.documents.service import DocumentService

        paste_id, _key, matter_id = await _photo(async_db_session)

        result = await file_document_execute(
            async_db_session, _named(paste_id, matter_id), None
        )

        document = await DocumentService(async_db_session).get(result["document_id"])
        assert document is not None
        assert (
            document.title,
            document.kind,
            document.form_type,
            document.tax_year,
        ) == (
            "Citizens Bank 1099-INT, 2025",
            "tax",
            "1099-INT",
            2025,
        )

    @pytest.mark.asyncio
    async def test_a_card_that_names_nothing_reads_as_before(
        self, async_db_session: AsyncSession
    ) -> None:
        paste_id, _key, matter_id = await _photo(async_db_session)
        payload = FileDocumentPayload(paste_id=paste_id, matter_id=matter_id)

        rows = await file_document_describe(async_db_session, payload, None)

        assert [r.label for r in rows] == ["Document", "File under"]
        assert rows[0].value == "IMG_6611.jpeg"


def test_the_prompt_names_paper_by_what_it_is() -> None:
    """The seed carries the tax rules a fresh install starts with."""
    from app.services.finance.domains.detection.analyst.prompts import (
        FINANCE_CHAT_SYSTEM_PROMPT,
    )

    prompt = FINANCE_CHAT_SYSTEM_PROMPT

    assert "## TAXES" in prompt
    assert "NEVER by its file name" in prompt
    assert "documents(matter_id=...)" in prompt
    assert '"tax_year"' in prompt  # document.file names what it files
