"""Opening a matter as a proposal (#284), the one part of a case that
had no card, and changing one already on file (#566): a note a
contractor's coming visit belongs in had nowhere to go.

She was asked twice - "can you create a case for this? ... a matter",
then "let's create a matter, and call it Alex's Root Canals" - and
twice could only say she could not. Its own module because
``changes`` is at its size budget.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.formatting import before_and_after
from app.services.finance.schemas import ChangeDisplayRow


class OpenMatterPayload(BaseModel):
    """A case opened from a conversation (#284): "let's create a matter,
    and call it Alex's Root Canals". Who it is about and who it is with
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
    from app.services.matters.matters import cleaned

    named = await _open_matter_parties(db, payload)
    rows = [
        ChangeDisplayRow(label="Matter", value=cleaned("title", payload.title) or "")
    ]
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


# What a card may change on a case, and what each is called on it.
AMENDABLE = {"title": "Title", "kind": "Kind", "reference": "Reference", "note": "Note"}


class AmendMatterPayload(BaseModel):
    """A change to a case already on file, shaped as ``contact.amend``:
    only what is sent changes, and "" clears it. A visit still to come
    goes in the note; once it has happened it is a ``matter.event``."""

    model_config = ConfigDict(extra="forbid")

    matter_id: int
    title: str | None = None
    kind: str | None = None
    reference: str | None = None
    note: str | None = None

    @field_validator("title")
    @classmethod
    def _titled(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("A matter keeps a title.")
        return value

    @model_validator(mode="after")
    def _says_something(self) -> AmendMatterPayload:
        if not self.sent():
            raise ValueError("An amendment has to name at least one field to change.")
        return self

    def sent(self) -> dict[str, str]:
        """The fields this card speaks about ("" is sent, and clears)."""
        return {
            field: value
            for field in AMENDABLE
            if (value := getattr(self, field)) is not None
        }


async def _amending(
    db: AsyncSession, payload: AmendMatterPayload
) -> tuple[Any, dict[str, str]]:
    """The case and what would change on it, refusing a reference another
    case carries, as opening one does."""
    from app.services.matters.matters import MatterService, cleaned

    service = MatterService(db)
    matter = await service.require(payload.matter_id)
    changed = {
        field: cleaned(field, value) or ""
        for field, value in payload.sent().items()
        if cleaned(field, value) != cleaned(field, getattr(matter, field))
    }
    if (reference := changed.get("reference")) and (
        taken := await service.by_reference(reference)
    ):
        raise ValueError(f"{taken.title} already has reference {reference}")
    if not changed:
        raise ValueError(
            f"This would change nothing about {matter.title}: every field sent "
            "already reads that way."
        )
    return matter, changed


async def amend_matter_execute(
    db: AsyncSession, payload: AmendMatterPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.matters import MatterService

    _, changed = await _amending(db, payload)
    matter = await MatterService(db).amend(payload.matter_id, changed)
    return {"matter_id": matter.id, "title": matter.title, "changed": sorted(changed)}


async def amend_matter_describe(
    db: AsyncSession, payload: AmendMatterPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    matter, changed = await _amending(db, payload)
    return [ChangeDisplayRow(label="Matter", value=matter.title)] + [
        ChangeDisplayRow(
            label=label,
            value=before_and_after(getattr(matter, field) or "", changed[field]),
        )
        for field, label in AMENDABLE.items()
        if field in changed
    ]
