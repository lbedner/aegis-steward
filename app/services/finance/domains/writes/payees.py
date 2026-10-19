"""Cleaning up a payee: its name, or two payees that are one (#328).

A merchant arriving lowercase, or named for the wrong brand, is fixed on
the payee once - every charge shows the payee's name - and two payees
for one merchant are folded together, their charges, bills and the
descriptors they were taught all following the one kept.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import merchants, queries
from app.services.finance.domains.writes.registry import ChangeExecutor
from app.services.finance.models import FinanceMerchant
from app.services.finance.schemas import ChangeDisplayRow
from app.services.finance.utils import normalize_payee


class PayeeRenamePayload(BaseModel):
    """A payee by the name it has, its new name, and its website when that
    is wrong too. By NAME, as a payee is assigned: ledger rows carry it."""

    model_config = ConfigDict(extra="forbid")

    payee: str = Field(min_length=1)
    name: str = Field(min_length=1)
    website_url: str | None = None


class PayeeMergePayload(BaseModel):
    """The payee kept, and the ones folded into it, by name."""

    model_config = ConfigDict(extra="forbid")

    keep: str = Field(min_length=1)
    merge: list[str] = Field(min_length=1)


async def _payee(
    db: AsyncSession, name: str, owner_user_id: int | None
) -> FinanceMerchant:
    found = await queries.merchant_by_normalized(
        db, normalized=normalize_payee(name), owner_user_id=owner_user_id
    )
    if found is None:
        raise ValueError(f"No payee named {name!r}.")
    return found


async def payee_rename_execute(
    db: AsyncSession, payload: PayeeRenamePayload, owner_user_id: int | None
) -> dict[str, Any]:
    payee = await _payee(db, payload.payee, owner_user_id)
    fields: dict[str, Any] = {"name": payload.name}
    if payload.website_url is not None:
        fields["website_url"] = payload.website_url
    await merchants.update_merchant(db, payee.id, owner_user_id=owner_user_id, **fields)
    return {"merchant_id": payee.id, "was": payload.payee, "now": payload.name}


async def payee_rename_describe(
    db: AsyncSession, payload: PayeeRenamePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    payee = await _payee(db, payload.payee, owner_user_id)
    rows = [
        ChangeDisplayRow(label="Payee", value=f"{payee.name} \u2192 {payload.name}")
    ]
    if payload.website_url is not None:
        rows.append(ChangeDisplayRow(label="Website", value=payload.website_url))
    return rows


async def _merge_parts(
    db: AsyncSession, payload: PayeeMergePayload, owner_user_id: int | None
) -> tuple[FinanceMerchant, list[FinanceMerchant]]:
    keep = await _payee(db, payload.keep, owner_user_id)
    merged = [await _payee(db, name, owner_user_id) for name in payload.merge]
    if any(m.id == keep.id for m in merged):
        raise ValueError("A payee cannot be merged into itself.")
    return keep, merged


async def payee_merge_execute(
    db: AsyncSession, payload: PayeeMergePayload, owner_user_id: int | None
) -> dict[str, Any]:
    keep, merged = await _merge_parts(db, payload, owner_user_id)
    ids = [m.id for m in merged if m.id is not None]
    moved = await merchants.merge_merchants(
        db, ids, keep.id, owner_user_id=owner_user_id
    )
    return {"merchant_id": keep.id, "merged": ids, "moved": moved}


async def payee_merge_describe(
    db: AsyncSession, payload: PayeeMergePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    keep, merged = await _merge_parts(db, payload, owner_user_id)
    return [
        ChangeDisplayRow(label="Keep", value=keep.name),
        ChangeDisplayRow(label="Merge in", value=", ".join(m.name for m in merged)),
    ]


# Registered by ``executors``, which arms every change type.
CHANGES = (
    ChangeExecutor(
        change_type="payee.rename",
        title="Rename a payee",
        payload_model=PayeeRenamePayload,
        execute=payee_rename_execute,
        describe=payee_rename_describe,
    ),
    ChangeExecutor(
        change_type="payee.merge",
        title="Merge payees into one",
        payload_model=PayeeMergePayload,
        execute=payee_merge_execute,
        describe=payee_merge_describe,
    ),
)
