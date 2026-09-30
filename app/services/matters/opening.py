"""Opening a matter as a proposal (#284): the one part of a case that
had no card.

She was asked twice - "can you create a case for this? ... a matter",
then "let's create a matter, and call it Marisa's Root Canals" - and
twice could only say she could not. Its own module because
``changes`` is at its size budget.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow


class OpenMatterPayload(BaseModel):
    """A case opened from a conversation (#284): "let's create a matter,
    and call it Marisa's Root Canals". Who it is about and who it is with
    are people already on file (parties()); somebody new is a
    contact.create first."""

    model_config = ConfigDict(extra="forbid")

    title: str
    kind: str | None = None
    # The other side's own number for the case, when they have one.
    reference: str | None = None
    subject_party_id: int | None = None
    counterpart_party_id: int | None = None
    opened_on: date | None = None
    note: str | None = None

    @field_validator("title")
    @classmethod
    def _titled(cls, value: str) -> str:
        if not " ".join((value or "").split()):
            raise ValueError("A matter needs a title.")
        return value


async def _open_matter_parties(
    db: AsyncSession, payload: OpenMatterPayload
) -> dict[str, Any]:
    """Who the card names, refusing what cannot be opened: somebody not
    on file, or a reference another case already carries (the agency's
    number is how two letters agree they are one case)."""
    from app.services.matters.matters import MatterService
    from app.services.matters.service import PartyService

    if payload.reference and (
        taken := await MatterService(db).by_reference(payload.reference)
    ):
        raise ValueError(f"{taken.title} already has reference {payload.reference}")
    named: dict[str, Any] = {}
    for field in ("subject_party_id", "counterpart_party_id"):
        party_id = getattr(payload, field)
        if party_id is None:
            continue
        named[field] = await PartyService(db).require(party_id)
    return named


async def open_matter_execute(
    db: AsyncSession, payload: OpenMatterPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.matters import MatterService

    await _open_matter_parties(db, payload)
    matter = await MatterService(db).open(
        title=payload.title,
        kind=payload.kind,
        reference=payload.reference,
        subject_party_id=payload.subject_party_id,
        counterpart_party_id=payload.counterpart_party_id,
        owner_user_id=owner_user_id,
        opened_on=payload.opened_on,
        note=payload.note,
    )
    return {"matter_id": matter.id, "title": matter.title}


async def open_matter_describe(
    db: AsyncSession, payload: OpenMatterPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    named = await _open_matter_parties(db, payload)
    rows = [ChangeDisplayRow(label="Matter", value=" ".join(payload.title.split()))]
    for label, value in (
        ("Kind", payload.kind),
        ("Reference", payload.reference),
        ("About", getattr(named.get("subject_party_id"), "name", None)),
        ("With", getattr(named.get("counterpart_party_id"), "name", None)),
        ("Opened", payload.opened_on and payload.opened_on.isoformat()),
        ("Note", payload.note),
    ):
        if value:
            rows.append(ChangeDisplayRow(label=label, value=value))
    return rows
