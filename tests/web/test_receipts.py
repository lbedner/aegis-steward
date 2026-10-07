"""A receipt is attached to its transaction, from the register (#331).

The row's menu attaches one (a PDF or a photo); the row then shows a
paperclip that opens it. The receipt is a Document on the shelf, filed
on the transaction, so it is also in Documents with everything else.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import transaction_tag
from app.services.finance.models import FinanceTransaction
from tests.web.conftest import Ledger
from tests.web.dom import none, one, triggers


async def _row(db: AsyncSession, account_id: int) -> int:
    found = (
        await db.exec(
            select(FinanceTransaction)
            .where(FinanceTransaction.account_id == account_id)
            .order_by(FinanceTransaction.id)
        )
    ).first()
    assert found is not None and found.id is not None
    return found.id


class TestAttachingFromTheRow:
    async def test_the_rows_menu_offers_it(
        self, client: TestClient, ledger: Ledger, async_db_session: AsyncSession
    ) -> None:
        txn = await _row(async_db_session, ledger.checking)
        row = one(client.get(f"/accounts/{ledger.checking}").text, f"#txn-{txn}")
        one(row, f'[hx-get="/transactions/{txn}/receipt"]')
        none(row, "[data-receipt]")

    async def test_the_dialog_takes_a_file(
        self, hx: TestClient, ledger: Ledger, async_db_session: AsyncSession
    ) -> None:
        txn = await _row(async_db_session, ledger.checking)
        form = one(hx.get(f"/transactions/{txn}/receipt").text, "form")
        assert form.get("hx-encoding") == "multipart/form-data"
        one(form, "input[type=file][name=file]")

    @pytest.mark.queryspy(threshold=3)  # the save, then the page
    async def test_attaching_files_it_and_marks_the_row(
        self,
        client: TestClient,
        hx: TestClient,
        ledger: Ledger,
        async_db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.documents.service import DocumentService

        async def _quietly(*_a: object, **_k: object) -> None:
            return None

        monkeypatch.setattr(
            "app.components.web_frontend.documents.read_quietly", _quietly
        )
        txn = await _row(async_db_session, ledger.checking)

        saved = hx.post(
            f"/transactions/{txn}/receipt",
            files={"file": ("check-1524.jpg", b"\xff\xd8\xff jpeg", "image/jpeg")},
        )

        assert "dialog:close" in triggers(saved)
        # The row comes back out of band (a template the swap unwraps),
        # wearing its paperclip.
        one(saved.text, f"template#txn-{txn}-oob")
        receipt = one(saved.text, "[data-receipt]")
        filed, _ = await DocumentService(async_db_session).list_documents(
            tag=transaction_tag(txn)
        )
        (document,) = filed
        assert receipt.get("hx-get") == f"/documents/{document.id}"
        # And it stays on the row when the page is drawn again.
        page = client.get(f"/accounts/{ledger.checking}").text
        one(page, f"#txn-{txn} [data-receipt]")

    async def test_no_file_is_asked_again(
        self, hx: TestClient, ledger: Ledger, async_db_session: AsyncSession
    ) -> None:
        txn = await _row(async_db_session, ledger.checking)
        refused = hx.post(f"/transactions/{txn}/receipt", data={})
        assert refused.status_code == 422
        one(refused.text, "[role=alert]")
