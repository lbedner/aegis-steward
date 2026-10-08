"""A combine card draws each photo as its page (#439)."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.queue import propose
from tests.web.conftest import Ledger
from tests.web.dom import one, select
from tests.web.test_check_review import _jpeg


@pytest.mark.asyncio
@pytest.mark.queryspy(threshold=4)  # the page, then two picture requests
async def test_each_page_is_drawn_and_served(
    client: TestClient, async_db_session: AsyncSession, ledger: Ledger
) -> None:
    from app.services.documents.service import DocumentService

    documents = DocumentService(async_db_session)
    ids = []
    for title, size in (("IMG_6611.jpeg", 0), ("IMG_6612.jpeg", 1)):
        photo = await documents.ingest(
            _jpeg() + bytes(size), title=title, media_type="image/jpeg"
        )
        ids.append(int(photo.id or 0))
    change = await propose(
        async_db_session,
        "document.combine",
        {"document_ids": ids, "title": "Citizens Bank 1099-INT, 2025"},
        owner_user_id=None,
    )
    await async_db_session.commit()

    card = one(client.get("/review").text, f"#change-{change.id}")
    srcs = [img.get("src") for img in select(card, "img[data-scan]")]
    assert srcs == [
        f"/review/changes/{change.id}/scan",
        f"/review/changes/{change.id}/scan?page=1",
    ]
    second = client.get(f"/review/changes/{change.id}/scan?page=1")
    assert second.status_code == 200 and second.content[:3] == b"\xff\xd8\xff"
    assert client.get(f"/review/changes/{change.id}/scan?page=2").status_code == 404


@pytest.mark.asyncio
async def test_a_documents_figures_are_shown_with_it(client: TestClient) -> None:
    """The boxes read off a form, beside the original (#442)."""
    from app.core.db import get_async_session
    from app.services.documents.service import DocumentService

    async with get_async_session() as db:
        documents = DocumentService(db)
        form = await documents.ingest(
            _jpeg(),
            title="Pure Proactive Health 1099-NEC, 2025",
            media_type="image/jpeg",
        )
        await documents.add_figures(
            int(form.id or 0), {"box 1 nonemployee compensation": "$86,380.00"}
        )
        await db.commit()

    figures = one(client.get(f"/documents/{form.id}").text, "dl[data-figures]")
    assert [t.text_content() for t in select(figures, "dt, dd")] == [
        "box 1 nonemployee compensation",
        "$86,380.00",
    ]
