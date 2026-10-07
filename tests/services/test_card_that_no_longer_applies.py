"""A card that no longer applies still draws, and can be rejected (#435).

A title card for a document somebody had named since made its describe
refuse ("This would change nothing about ..."), and that one card took
the whole Approvals page down with a 500 - and could not be rejected,
because rejecting freezes the card's display first.
"""

from __future__ import annotations

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.service import DocumentService
from app.services.finance.domains.writes.queue import describe_change, propose, reject


async def _stale_title_card(db: AsyncSession) -> int:
    document = await DocumentService(db).ingest(
        b"%PDF-1.4 1099", title="IMG_6611.jpeg", media_type="application/pdf"
    )
    await DocumentService(db).update(
        int(document.id or 0), {"title": "Citizens Form 1099-INT, January 2026"}
    )
    card = await propose(
        db,
        "document.metadata",
        {
            "document_id": int(document.id or 0),
            "title": {
                "value": "Citizens Form 1099-INT, 2025",
                "page": 1,
                "because": "Citizens Bank N.A.",
            },
        },
        owner_user_id=None,
        proposed_by_agent="reading",
    )
    await db.flush()
    return int(card.id or 0)


class TestACardThatNoLongerApplies:
    @pytest.mark.asyncio
    async def test_it_says_why_instead_of_raising(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.queue import get_change

        card = await get_change(
            async_db_session, await _stale_title_card(async_db_session)
        )
        assert card is not None

        rows = await describe_change(async_db_session, card)

        said = {r.label: r.value for r in rows}
        assert said["Change"] == "What a document says it is"
        assert "would change nothing" in said["No longer applies"]

    @pytest.mark.asyncio
    async def test_it_can_be_rejected(self, async_db_session: AsyncSession) -> None:
        card_id = await _stale_title_card(async_db_session)

        rejected = await reject(async_db_session, card_id)

        assert rejected.status == "rejected"
