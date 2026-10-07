"""A receipt is attached to its transaction (#331).

The receipt stays with the charge it explains: a Document on the shelf,
filed on the transaction by a tag (``transaction_tag``) the way a
statement is filed on its account. ``document.file`` - the card that
files a chat attachment - takes a transaction as its fourth place.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.constants import tagged_transaction, transaction_tag
from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account


async def _check(svc: FinanceService) -> int:
    account = await seed_account(svc, name="TOTAL CHECKING")
    assert account.id is not None
    row = await svc.create_transaction(
        owner_user_id=1,
        account_id=account.id,
        amount=-2500,
        txn_date=date(2025, 7, 1),
        name="CHECK # 1524",
        source="csv",
    )
    assert row.id is not None
    return row.id


def test_the_label_names_its_transaction_and_nothing_else() -> None:
    assert transaction_tag(39057) == "transaction:39057"
    assert tagged_transaction(transaction_tag(39057)) == 39057
    assert tagged_transaction("account:39057") is None
    assert tagged_transaction("transaction:x") is None


class TestFiledFromChat:
    @pytest.mark.asyncio
    async def test_an_attachment_is_filed_on_its_transaction(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        from app.services.ai.domains.chat.pastes import store_document
        from app.services.documents.service import DocumentService
        from app.services.finance.domains.writes.filing import (
            FileDocumentPayload,
            file_document_describe,
            file_document_execute,
        )

        txn_id = await _check(svc)
        documents = DocumentService(async_db_session)
        document = await documents.ingest(
            b"%PDF-1.4 check 1524", title="check-1524.pdf"
        )
        paste = await store_document(
            "0", int(document.id), "check-1524.pdf", 20, async_db_session
        )
        payload = FileDocumentPayload(paste_id=str(paste["id"]), transaction_id=txn_id)

        rows = {
            r.label: r.value
            for r in await file_document_describe(async_db_session, payload, None)
        }
        await file_document_execute(async_db_session, payload, None)

        # Named on the card as the row it is, never as an id.
        assert rows["File under"] == "CHECK # 1524 ($25.00 on Jul 1, 2025)"
        filed, _ = await documents.list_documents(tag=transaction_tag(txn_id))
        assert [d.title for d in filed] == ["check-1524.pdf"]

    def test_one_place_still_means_one(self) -> None:
        from pydantic import ValidationError

        from app.services.finance.domains.writes.filing import FileDocumentPayload

        with pytest.raises(ValidationError):
            FileDocumentPayload(paste_id="p", transaction_id=1, account_id=2)
