"""The card a check's scans arrive on (#415): ``check.attach``.

Split from ``checks`` - which finds a check, reads it and matches its
row - at the budget. Approving files the scans on the row as its receipt
and names its payee; nothing is filed until then.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow


class CheckPayload(BaseModel):
    """One check's scans for its ledger row, and the payee read off its
    face where the row has none. Keys name the scans in storage."""

    model_config = ConfigDict(extra="forbid")

    document_id: int
    page: int
    slot: int
    transaction_id: int
    number: str
    front_key: str
    back_key: str | None = None
    # Text, so it can be put right before approving (#420): by you on
    # the card, or by Illiana. ``payee_because`` is the line it was read
    # from, which stays when the name is corrected.
    payee: str | None = None
    payee_because: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _read_value_payee(cls, data: Any) -> Any:
        """A card filed while the payee was a cited reading (#415) reads
        the same: ``{"value", "page", "because"}`` becomes the two."""
        if isinstance(data, dict) and isinstance(data.get("payee"), dict):
            read = data["payee"]
            data = {
                **data,
                "payee": read.get("value"),
                "payee_because": data.get("payee_because") or read.get("because"),
            }
        return data


async def check_execute(
    db: AsyncSession, payload: CheckPayload, owner_user_id: int | None
) -> dict[str, Any]:
    """File the scans on the row as its receipt; name its payee."""
    from app.core.storage import get_storage
    from app.services.documents.service import DocumentService
    from app.services.finance.constants import transaction_tag
    from app.services.finance.domains.writes.curation import (
        AssignPayeePayload,
        assign_payee_execute,
    )

    store = get_storage()
    documents = DocumentService(db)
    filed: list[int] = []
    for key, title in (
        (payload.front_key, f"Check {payload.number}"),
        (payload.back_key, f"Check {payload.number} (back)"),
    ):
        if key is None:
            continue
        data = await store.get(key)
        if data is None:
            raise ValueError(f"The scan of check {payload.number} is gone; read again.")
        document = await documents.ingest(
            data,
            title=title,
            kind="receipt",
            media_type="image/jpeg",
            owner_user_id=owner_user_id,
            source="reading",
        )
        await documents.tag(
            int(document.id or 0), transaction_tag(payload.transaction_id)
        )
        filed.append(int(document.id or 0))
    if payload.payee:
        await assign_payee_execute(
            db,
            AssignPayeePayload(
                transaction_id=payload.transaction_id, payee=payload.payee
            ),
            owner_user_id,
        )
    await db.flush()
    return {"transaction_id": payload.transaction_id, "documents": filed}


async def check_describe(
    db: AsyncSession, payload: CheckPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.writes.display import txn_row

    _txn, subject = await txn_row(db, payload.transaction_id, owner_user_id)
    rows = [
        subject,
        ChangeDisplayRow(
            label="Check",
            value=f"#{payload.number}, front and back",
            note=f"page {payload.page}",
            document_id=payload.document_id,
            page=payload.page,
        ),
    ]
    if payload.payee:
        rows.append(
            ChangeDisplayRow(
                label="Payee",
                value=f"- → {payload.payee}",
                note=f"page {payload.page}: {payload.payee_because}"
                if payload.payee_because
                else None,
                document_id=payload.document_id,
                page=payload.page,
            )
        )
    return rows
