"""Matter writes as proposals: what Illiana can put in front of you.

Working a matter adds CHANGE TYPES, not tools. The one write tool is
``propose``, and it is change-type generic - so a fact she reads off a
letter arrives as a card you approve, the same way a category or a
valuation does, and nothing she says is true until you say so.

Registered into the finance write registry because that is where the
propose/approve machinery lives. This is its second consumer and the
first from outside finance, which is the count ST-08 said to wait for
before deciding whether that machinery moves somewhere shared. The seam
is visible here on purpose rather than papered over with a copy.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow
from app.services.matters.facts import ATTRIBUTE_KEYS, LABELS, monthly_cents
from app.services.matters.models import FACT_PERIODS, FACT_PROVENANCE


class RecordFactPayload(BaseModel):
    """A claim about somebody's money, with where it came from.

    The provenance is required rather than defaulted, because the whole
    point of the row is that a deposit, a statement and a benefit letter
    are three different claims and only one of them answers a form.
    """

    model_config = ConfigDict(extra="forbid")

    subject_party_id: int
    attribute: str
    provenance: str
    label: str | None = None
    value_cents: int | None = Field(default=None, ge=0)
    period: str = "once"
    text_value: str | None = None
    as_of: date | None = None
    matter_id: int | None = None
    document_id: int | None = None
    page: int | None = None
    source_party_id: int | None = None
    source_url: str | None = None
    source_note: str | None = None
    note: str | None = None

    @field_validator("attribute")
    @classmethod
    def _known_attribute(cls, value: str) -> str:
        if value not in ATTRIBUTE_KEYS:
            raise ValueError(f"One of: {', '.join(ATTRIBUTE_KEYS)}.")
        return value

    @field_validator("period")
    @classmethod
    def _known_period(cls, value: str) -> str:
        if value not in FACT_PERIODS:
            raise ValueError(f"One of: {', '.join(FACT_PERIODS)}.")
        return value

    @field_validator("provenance")
    @classmethod
    def _known_provenance(cls, value: str) -> str:
        if value not in FACT_PROVENANCE:
            raise ValueError(f"One of: {', '.join(FACT_PROVENANCE)}.")
        return value


async def record_fact_execute(
    db: AsyncSession, payload: RecordFactPayload, owner_user_id: int | None
) -> dict[str, Any]:
    """File the fact. Never verified: the flag means a person checked it
    against the source, and approving a card is approving what was
    READ, not confirming the source was opened."""
    from app.services.matters.facts import FactService

    fact = await FactService(db).record(
        subject_party_id=payload.subject_party_id,
        matter_id=payload.matter_id,
        attribute=payload.attribute,
        label=payload.label,
        value_cents=payload.value_cents,
        period=payload.period,
        text_value=payload.text_value,
        as_of=payload.as_of,
        provenance=payload.provenance,
        document_id=payload.document_id,
        page=payload.page,
        source_party_id=payload.source_party_id,
        source_url=payload.source_url,
        source_note=payload.source_note,
        owner_user_id=owner_user_id,
        note=payload.note,
    )
    await db.flush()
    return {"fact_id": fact.id, "attribute": fact.attribute}


async def record_fact_describe(
    db: AsyncSession, payload: RecordFactPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    """The card: whose, what, how much, and how it is known - because
    the source is what makes the number usable, and a card that hides it
    is asking for approval of half the claim."""
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.matters.service import PartyService

    parties = PartyService(db)
    subject = await parties.get(payload.subject_party_id)
    rows = [
        ChangeDisplayRow(label="About", value=subject.name if subject else "Unknown"),
        ChangeDisplayRow(
            label=LABELS.get(payload.attribute, payload.attribute),
            value=payload.label or "-",
        ),
    ]
    if payload.value_cents is not None:
        said = format_usd(payload.value_cents)
        if payload.period != "once":
            said = f"{said} a {payload.period}"
        monthly = monthly_cents(payload.value_cents, payload.period)
        if monthly is not None and payload.period not in ("once", "month"):
            said = f"{said} (about {format_usd(monthly)} a month)"
        rows.append(ChangeDisplayRow(label="Figure", value=said))
    if payload.text_value:
        rows.append(ChangeDisplayRow(label="Says", value=payload.text_value))
    if payload.as_of:
        rows.append(ChangeDisplayRow(label="As of", value=payload.as_of.isoformat()))
    source = payload.provenance
    if payload.source_party_id:
        place = await parties.get(payload.source_party_id)
        if place is not None:
            source = f"{source} · {place.name}"
    if payload.source_note:
        source = f"{source} · {payload.source_note}"
    rows.append(ChangeDisplayRow(label="How it is known", value=source))
    return rows
