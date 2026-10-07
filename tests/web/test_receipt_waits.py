"""A waiting receipt's card shows the receipt beside its charge (#330)."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading import receipts
from app.services.documents.models import DocumentPage
from app.services.documents.service import DocumentService
from app.services.finance.service import FinanceService
from tests.web.conftest import Ledger
from tests.web.dom import one, text


@pytest.fixture
async def receipt_card(async_db_session: AsyncSession, ledger: Ledger) -> int:
    db = async_db_session
    document = await DocumentService(db).ingest(
        b"%PDF-1.4 cvs",
        title="CVS receipt",
        media_type="application/pdf",
        kind="receipt",
    )
    document_id = int(document.id or 0)
    db.add(
        DocumentPage(
            document_id=document_id,
            page_number=1,
            status="read",
            method="text_layer",
            text="CVS pharmacy 09/03/2026\nTOTAL 10.80\n",
        )
    )
    rows, _ = await FinanceService(db).list_transactions(
        owner_user_id=None, page_size=1
    )
    await FinanceService(db).create_transaction(
        owner_user_id=1,
        account_id=int(rows[0].account_id),
        amount=-1080,
        txn_date=date(2026, 9, 4),
        name="CVS/PHARMACY",
        source="csv",
    )
    assert await receipts.match_waiting(db, owner_user_id=None) == 1
    await db.commit()
    return document_id


class TestTheCard:
    def test_the_receipt_is_drawn_beside_its_charge(
        self, client: TestClient, receipt_card: int
    ) -> None:
        page = client.get("/review").text
        card = one(page, "li[id^=batch-]")
        assert "A receipt on its charge" in text(card)
        assert "CVS receipt, $10.80" in text(card)
        drawn = one(card, "img[data-page]")
        assert drawn.get("src") == f"/api/v1/documents/{receipt_card}/pages/1/image"
