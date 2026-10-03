"""Filing a transaction with a matter or a contact (#290, #303).

"I don't have a tool to link it directly to a specific matter": a
$1,500 root canal charge belonged on the case, and a dentist's charges
with the dentist. ``transaction.link`` files one transaction under the
place's label (``links``), or with ``unlink`` takes it back off.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import links
from app.services.finance.domains.writes.display import txn_row
from app.services.finance.domains.writes.filing import place
from app.services.finance.schemas import ChangeDisplayRow


class LinkTransactionPayload(BaseModel):
    """Which transaction belongs to which case or contact - exactly one
    of them - or, with ``unlink``, no longer does."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: int
    party_id: int | None = None
    matter_id: int | None = None
    unlink: bool = False

    @model_validator(mode="after")
    def _one_place(self) -> LinkTransactionPayload:
        if (self.party_id is None) == (self.matter_id is None):
            raise ValueError("Exactly one of party_id, matter_id.")
        return self


async def _where(
    db: AsyncSession, payload: LinkTransactionPayload, owner_user_id: int | None
) -> tuple[str, str]:
    return await place(
        db, owner_user_id, party_id=payload.party_id, matter_id=payload.matter_id
    )


async def link_transaction_execute(
    db: AsyncSession, payload: LinkTransactionPayload, owner_user_id: int | None
) -> dict[str, Any]:
    txn, _row = await txn_row(db, payload.transaction_id, owner_user_id)
    if txn is None:
        raise ValueError(f"Transaction {payload.transaction_id} not found.")
    label, _name = await _where(db, payload, owner_user_id)
    if payload.unlink:
        await links.unlink(db, payload.transaction_id, label)
        return {"transaction_id": payload.transaction_id, "unlinked_from": label}
    await links.link(db, payload.transaction_id, label)
    return {"transaction_id": payload.transaction_id, "linked_to": label}


async def link_transaction_describe(
    db: AsyncSession, payload: LinkTransactionPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    _txn, row = await txn_row(db, payload.transaction_id, owner_user_id)
    try:
        _label, name = await _where(db, payload, owner_user_id)
    except ValueError as exc:
        name = str(exc)
    where = "Unlink from" if payload.unlink else "Link to"
    return [row, ChangeDisplayRow(label=where, value=name)]
