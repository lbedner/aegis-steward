"""Photos that are pages of one form become one document (#439).

Eight photos of 2025 tax forms became eight documents, and Illiana
counted eight: they were four forms, a 1099-INT's front and back among
them and a 1095-C over four photos.
"""

from __future__ import annotations

import io
from typing import Any

from pydantic import ValidationError
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.combining import (
    CombinePayload,
    combine_describe,
    combine_execute,
    combine_scan,
)


def _jpeg(color: str) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (850, 1100), color).save(out, "JPEG")
    return out.getvalue()


async def _photo(db: AsyncSession, title: str, color: str, text: str) -> int:
    """A photo on the shelf, read, filed on the tax matter."""
    from app.core.storage import get_storage
    from app.services.documents.models import DocumentPage
    from app.services.documents.service import DocumentService

    data = _jpeg(color)
    documents = DocumentService(db)
    photo = await documents.ingest(data, title=title, media_type="image/jpeg")
    db.add(
        DocumentPage(
            document_id=int(photo.id or 0),
            page_number=1,
            status="read",
            method="vision",
            text=text,
            image_key=photo.storage_key,
        )
    )
    await documents.tag(int(photo.id or 0), "matter:3")
    await db.flush()
    assert await get_storage().get(photo.storage_key) == data
    return int(photo.id or 0)


def _named(**over: Any) -> CombinePayload:
    return CombinePayload(
        **{
            "document_ids": [1, 2],
            "title": "Citizens Bank 1099-INT, 2025",
            "kind": "tax",
            "form_type": "1099-INT",
            "tax_year": 2025,
            **over,
        }
    )


class TestTheCard:
    def test_it_takes_two_pages_or_more_from_one_place(self) -> None:
        with pytest.raises(ValidationError, match="two"):
            _named(document_ids=[1])
        with pytest.raises(ValidationError, match="once"):
            _named(document_ids=[1, 1])
        with pytest.raises(ValidationError, match="either"):
            _named(paste_ids=["a", "b"])
        with pytest.raises(ValidationError, match="1099-XYZ"):
            _named(form_type="1099-XYZ")

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the card drawn, then its pictures
    async def test_it_shows_each_photo_as_its_page(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import DocumentService

        front = await _photo(
            async_db_session, "IMG_6611.jpeg", "white", "Form 1099-INT"
        )
        back = await _photo(
            async_db_session, "IMG_6612.jpeg", "gray", "Instructions for Recipient"
        )
        payload = _named(document_ids=[front, back])

        rows = await combine_describe(async_db_session, payload, None)

        said = [(r.label, r.value, r.scan) for r in rows]
        assert said[:3] == [
            ("Document", "Citizens Bank 1099-INT, 2025", False),
            ("Page 1", "IMG_6611.jpeg", True),
            ("Page 2", "IMG_6612.jpeg", True),
        ]
        documents = DocumentService(async_db_session)
        keys = [(await documents.get(i)).storage_key for i in (front, back)]  # type: ignore[union-attr]
        assert await combine_scan(async_db_session, payload, None) == keys


class TestApproving:
    @pytest.mark.asyncio
    async def test_the_photos_become_one_named_document_and_retire(
        self, async_db_session: AsyncSession
    ) -> None:
        import pypdfium2

        from app.core.storage import get_storage
        from app.services.documents.queries import pages_for, tags_for_many
        from app.services.documents.service import DocumentService

        db = async_db_session
        front = await _photo(db, "IMG_6611.jpeg", "white", "Form 1099-INT")
        back = await _photo(db, "IMG_6612.jpeg", "gray", "Instructions for Recipient")
        await DocumentService(db).tag(back, "party:9")

        result = await combine_execute(db, _named(document_ids=[front, back]), None)

        documents = DocumentService(db)
        made = await documents.get(result["document_id"])
        assert made is not None
        assert (made.title, made.kind, made.form_type, made.tax_year) == (
            "Citizens Bank 1099-INT, 2025",
            "tax",
            "1099-INT",
            2025,
        )
        assert (made.media_type, made.page_count) == ("application/pdf", 2)
        pdf = pypdfium2.PdfDocument(await get_storage().get(made.storage_key))
        assert len(pdf) == 2
        # Read once, as photos: the text and the picture of each page carry.
        pages = await pages_for(db, int(made.id or 0))
        assert [(p.page_number, p.text) for p in pages] == [
            (1, "Form 1099-INT"),
            (2, "Instructions for Recipient"),
        ]
        assert all(p.image_key for p in pages)
        assert result["read"] is False
        # Every place either photo was filed, the new document is.
        tags = (await tags_for_many(db, [int(made.id or 0)]))[int(made.id or 0)]
        assert set(tags) >= {"matter:3", "party:9"}
        # The photos are retired, not gone.
        assert await documents.get(front) is None and await documents.get(back) is None
        assert sorted(result["retired"]) == sorted([front, back])

    @pytest.mark.asyncio
    async def test_photos_from_the_chat_are_filed_as_one_and_read(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.core.storage import get_storage
        from app.services.ai.domains.chat.pastes import store_image
        from app.services.documents.queries import tags_for_many
        from app.services.matters.matters import MatterService
        from app.services.matters.models import matter_tag

        db = async_db_session
        matter = await MatterService(db).open(title="2025 Tax Return")
        ids = []
        for name, color in (("IMG_7001.jpeg", "white"), ("IMG_7002.jpeg", "gray")):
            key = await get_storage().put(_jpeg(color), content_type="image/jpeg")
            ids.append((await store_image("0", key, "image/jpeg", name, db))["id"])

        result = await combine_execute(
            db,
            CombinePayload(
                paste_ids=ids, title="Form 1098, 2025", matter_id=int(matter.id or 0)
            ),
            None,
        )

        made = result["document_id"]
        assert (
            matter_tag(int(matter.id or 0)) in (await tags_for_many(db, [made]))[made]
        )
        # Never read as photos: read now, after the approval commits.
        assert result["read"] is True


class TestTheFiguresCarry:
    @pytest.mark.asyncio
    async def test_the_photos_figures_are_the_documents(
        self, async_db_session: AsyncSession
    ) -> None:
        """A combine that names no figures keeps the ones on its photos,
        and one that does wins (#442)."""
        from app.services.documents.service import DocumentService

        db = async_db_session
        front = await _photo(db, "IMG_6611.jpeg", "white", "Form 1099-INT")
        back = await _photo(db, "IMG_6612.jpeg", "gray", "Instructions for Recipient")
        documents = DocumentService(db)
        await documents.add_figures(front, {"box 1 interest income": "$127.78"})

        kept = await combine_execute(db, _named(document_ids=[front, back]), None)

        made = await documents.get(kept["document_id"])
        assert made is not None
        assert made.meta_data["figures"] == {"box 1 interest income": "$127.78"}

    def test_a_card_can_say_them(self) -> None:
        payload = _named(figures={"box 1 interest income": "$127.78"})
        assert payload.figures == {"box 1 interest income": "$127.78"}
