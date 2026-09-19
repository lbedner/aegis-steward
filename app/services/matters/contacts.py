"""Naming a contact, and correcting one already named.

Its own module at the budget, and the seam is a real one: ``changes``
is what happens inside a MATTER - a fact read off a letter, an ask the
letter makes, the paper that answers it - and this is the address book
those all point at. A contact outlives every matter it appears in.

Two change types, and the pair is the point. ``contact.create`` existed
alone for a while, which meant a detail learned about somebody already
on file had nowhere to go: it got announced as saved and written
nowhere. Naming somebody and correcting them are the same job seen
twice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.schema import known
from app.services.finance.schemas import ChangeDisplayRow
from app.services.matters.models import PARTY_KINDS
from app.services.matters.reach import (
    CONTACT_FIELDS,
    CONTACT_LINES,
    one_home,
    reach_lines,
)


class CreateContactPayload(BaseModel):
    """A person or an organization named for the first time, with how
    to reach them. Never deduplicated by name: two "Bedner"s are two
    people until somebody says otherwise."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str = "person"
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    # Everything else they print: a Spanish line, a number from abroad,
    # a claims fax. Labelled because a number nobody labelled is a
    # number whose meaning is lost.
    also: list[dict[str, str]] = Field(default_factory=list)
    note: str | None = None

    _known_kind = field_validator("kind")(known(PARTY_KINDS))

    @model_validator(mode="after")
    def _one_home(self) -> CreateContactPayload:
        one_home(self.reach(), self.note)
        return self

    @field_validator("name")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A contact needs a name.")
        return value

    def reach(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            key: value.strip()
            for key, _label in CONTACT_FIELDS
            if (value := getattr(self, key)) and value.strip()
        }
        if lines := [
            {"label": label, "value": value}
            for label, value in reach_lines({CONTACT_LINES: self.also})
        ]:
            found[CONTACT_LINES] = lines
        return found


async def create_contact_execute(
    db: AsyncSession, payload: CreateContactPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.service import PartyService

    party = await PartyService(db).create(
        name=payload.name,
        kind=payload.kind,
        owner_user_id=owner_user_id,
        contact=payload.reach(),
        note=payload.note,
    )
    await db.flush()
    return {"party_id": party.id, "name": party.name}


async def create_contact_describe(
    db: AsyncSession, payload: CreateContactPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    rows = [
        ChangeDisplayRow(label="Contact", value=payload.name),
        ChangeDisplayRow(label="Kind", value=payload.kind),
    ]
    labels = dict(CONTACT_FIELDS)
    reach = payload.reach()
    rows.extend(
        ChangeDisplayRow(label=labels[key], value=reach[key])
        for key, _label in CONTACT_FIELDS
        if key in reach
    )
    # Each labelled line gets its own row: a card that folds three phone
    # numbers into one line is a card nobody checks.
    rows.extend(
        ChangeDisplayRow(label=label, value=value)
        for label, value in reach_lines(reach)
    )
    if payload.note:
        rows.append(ChangeDisplayRow(label="Note", value=payload.note))
    return rows


class AmendContactPayload(BaseModel):
    """A correction to a contact that already exists.

    ``contact.create`` could name somebody and nothing could correct
    them, so a phone number learned about a person already on file had
    nowhere to go - and what happened instead was that it got announced
    as saved and written nowhere. Delta Dental's numbers went into the
    note for the same reason and could not be moved out.

    ONLY WHAT IS SENT CHANGES. An unset field is a field this card says
    nothing about, which is what makes it safe to propose a phone number
    without restating an address nobody asked about. ``None`` and
    "not sent" are therefore the same thing.

    An EMPTY STRING is different: it means "this should be blank". A bad
    reading once put a website of "state.ny.us" and a truncated
    caseworker email onto a county department, and nothing could take
    them off again - a record you can fill and cannot empty accumulates
    every mistake ever made in it (2026-09-18).
    """

    model_config = ConfigDict(extra="forbid")

    party_id: int
    name: str | None = None
    # What a reader looks UNDER, which a rename does not change: see
    # PartyService.update. Refiling is its own decision.
    sort_name: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    also: list[dict[str, str]] | None = None
    note: str | None = None
    # Where each changed line was read: a document and page, or a URL.
    # Keyed by field name, so one lookup that yields three fields can
    # cite itself three times without three nested objects. A plain
    # string until a lookup exists that could produce a link.
    sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _named(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("A contact needs a name.")
        return value

    @model_validator(mode="after")
    def _says_something(self) -> AmendContactPayload:
        """A card with nothing on it is a card somebody has to read
        before working out it was never about anything."""
        if not self.sent():
            raise ValueError("An amendment has to name at least one field to change.")
        return self

    @model_validator(mode="after")
    def _one_home(self) -> AmendContactPayload:
        one_home(self.sent(), self.note)
        return self

    def sent(self) -> dict[str, Any]:
        """The fields this card speaks about, reach and all."""
        fields = ("name", "sort_name", "note", *[k for k, _ in CONTACT_FIELDS])
        # ``is not None`` is the whole test: "" is SENT and means clear.
        found: dict[str, Any] = {
            key: value
            for key in fields
            if (value := getattr(self, key)) is not None
        }
        if self.also is not None:
            found[CONTACT_LINES] = [
                {"label": label, "value": value}
                for label, value in reach_lines({CONTACT_LINES: self.also})
            ]
        return found


def _amended(party: Any, payload: AmendContactPayload) -> dict[str, Any]:
    """The contact block as it would be, and the party fields with it.

    One reading of the payload for both the card and the write, because
    a card that describes a different change from the one it performs is
    worse than no card at all.
    """
    sent = payload.sent()
    contact = dict(party.contact or {})
    changes: dict[str, Any] = {}
    for key, value in sent.items():
        if key in ("name", "sort_name", "note"):
            changes[key] = value
        elif str(value).strip():
            contact[key] = value
        else:
            # Emptied, not blanked: a key whose value is "" would read
            # back as a field that exists and is empty, which is not the
            # same as one nobody has recorded.
            contact.pop(key, None)
    if sent:
        changes["contact"] = contact
    return changes


async def _contact_being_amended(db: AsyncSession, party_id: int) -> Any:
    from app.services.matters.service import PartyService

    party = await PartyService(db).get(party_id)
    if party is None:
        raise ValueError(f"No contact with id {party_id}")
    return party


async def amend_contact_execute(
    db: AsyncSession, payload: AmendContactPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.matters.service import PartyService

    party = await _contact_being_amended(db, payload.party_id)
    changes = _amended(party, payload)
    await PartyService(db).update(payload.party_id, changes)
    return {"party_id": party.id, "name": party.name, "changed": sorted(changes)}


def _before_and_after(before: str, after: str, source: str) -> str:
    """One row's worth: what it was, what it becomes, where it came from.

    A dash for absent on either side, because a blank half reads as a
    card that forgot to say something rather than a field that was
    empty - and "empty" is exactly what the reader has to weigh.
    """
    line = f"{before or '-'} → {after or '-'}"
    return f"{line} · {source}" if source else line


async def amend_contact_describe(
    db: AsyncSession, payload: AmendContactPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    party = await _contact_being_amended(db, payload.party_id)
    sent = payload.sent()
    was = dict(party.contact or {})
    rows = [ChangeDisplayRow(label="Contact", value=party.name)]
    for key in ("name", "sort_name", "note"):
        if key in sent and str(sent[key] or "") != str(getattr(party, key) or ""):
            rows.append(
                ChangeDisplayRow(
                    label={"name": "Name", "sort_name": "Files under"}.get(key, "Note"),
                    value=_before_and_after(
                        getattr(party, key) or "",
                        sent[key] or "",
                        payload.sources.get(key, ""),
                    ),
                )
            )
    for key, label in CONTACT_FIELDS:
        # Absent and empty are the same thing to compare against, so
        # clearing a field that was never there changes nothing.
        if key in sent and (sent[key] or "") != (was.get(key) or ""):
            rows.append(
                ChangeDisplayRow(
                    label=label,
                    value=_before_and_after(
                        was.get(key, ""), sent[key], payload.sources.get(key, "")
                    ),
                )
            )
    # Sending ``also`` REPLACES the labelled lines, so a line that is
    # about to disappear gets a row saying so. A silent drop is how a
    # claims fax nobody re-typed stops existing.
    if CONTACT_LINES in sent:
        rows.extend(_line_rows(was, sent, payload))
    if len(rows) == 1:
        raise ValueError(
            f"This would change nothing about {party.name}: every field sent "
            "already reads that way."
        )
    return rows


def _line_rows(
    was: dict[str, Any], sent: dict[str, Any], payload: AmendContactPayload
) -> list[ChangeDisplayRow]:
    before = dict(reach_lines(was))
    after = dict(reach_lines({CONTACT_LINES: sent[CONTACT_LINES]}))
    return [
        ChangeDisplayRow(
            label=label,
            value=_before_and_after(
                before.get(label, ""),
                after.get(label, ""),
                payload.sources.get(CONTACT_LINES, ""),
            ),
        )
        for label in [*before, *[k for k in after if k not in before]]
        if before.get(label) != after.get(label)
    ]
