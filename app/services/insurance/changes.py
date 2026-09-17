"""Insurance writes as proposals: ``policy.create`` and ``claim.record``.

The plan document proposes the policy; the EOB proposes the claim. Both
are cards, the way every other write is, and nothing is true until it is
approved.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.schemas import ChangeDisplayRow
from app.services.insurance.models import CLAIM_STATUSES, POLICY_KINDS


class PolicyCreatePayload(BaseModel):
    """What the plan documents say, as the columns hold it. ``terms`` is
    the labelled lines the documents use - "Annual maximum": "$2,000 per
    member" - because they differ by kind."""

    model_config = ConfigDict(extra="forbid")

    insurer_party_id: int
    covered_party_ids: list[int] = Field(min_length=1)
    kind: str
    name: str | None = None
    policy_number: str | None = None
    member_id: str | None = None
    group_id: str | None = None
    effective_on: date | None = None
    renews_on: date | None = None
    premium_stream_id: int | None = None
    terms: dict[str, str] = Field(default_factory=dict)
    note: str | None = None

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in POLICY_KINDS:
            raise ValueError(f"One of: {', '.join(POLICY_KINDS)}.")
        return value


class ClaimRecordPayload(BaseModel):
    """One visit as the EOB settled it. The EOB itself rides as the
    paste id the conversation holds, or a document id off the shelf."""

    model_config = ConfigDict(extra="forbid")

    policy_id: int
    covered_party_id: int
    service_on: date
    provider_party_id: int | None = None
    claim_number: str | None = None
    status: str = "processed"
    billed_cents: int = Field(default=0, ge=0)
    allowed_cents: int = Field(default=0, ge=0)
    insurer_paid_cents: int = Field(default=0, ge=0)
    patient_owes_cents: int = Field(default=0, ge=0)
    paste_id: str | None = None
    document_id: int | None = None
    note: str | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in CLAIM_STATUSES:
            raise ValueError(f"One of: {', '.join(CLAIM_STATUSES)}.")
        return value

    @model_validator(mode="after")
    def _one_document(self) -> ClaimRecordPayload:
        if self.paste_id is not None and self.document_id is not None:
            raise ValueError("Either paste_id or document_id, not both.")
        return self


async def _names(db: AsyncSession, ids: list[int]) -> dict[int, str]:
    from sqlmodel import col, select

    from app.services.matters.models import Party

    if not ids:
        return {}
    rows = (await db.exec(select(Party).where(col(Party.id).in_(ids)))).all()
    return {int(p.id): p.name for p in rows}


async def _eob(
    db: AsyncSession, payload: ClaimRecordPayload, owner_user_id: int | None
) -> tuple[int | None, str | None]:
    """The EOB as (document_id, title): a paste resolved, a shelf id
    checked, or nothing."""
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.writes.filing import _filed

    if payload.paste_id is not None:
        found = await _filed(db, payload.paste_id, owner_user_id)
        if found is None:
            raise ValueError(
                f"{payload.paste_id!r} is not an attached document; pasted "
                "text cannot be the EOB."
            )
        return found
    if payload.document_id is not None:
        document = await DocumentService(db).get(payload.document_id)
        if document is None:
            raise ValueError(f"No document with id {payload.document_id}")
        return int(document.id), document.title
    return None, None


async def policy_create_execute(
    db: AsyncSession, payload: PolicyCreatePayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.insurance.service import InsuranceService
    from app.services.matters.service import PartyService

    if await PartyService(db).get(payload.insurer_party_id) is None:
        raise ValueError(f"No contact with id {payload.insurer_party_id}")
    policy = await InsuranceService(db).create_policy(
        owner_user_id=owner_user_id,
        **payload.model_dump(),
    )
    return {"policy_id": policy.id}


async def policy_create_describe(
    db: AsyncSession, payload: PolicyCreatePayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    names = await _names(db, [payload.insurer_party_id, *payload.covered_party_ids])
    rows = [
        ChangeDisplayRow(
            label="Insurer", value=names.get(payload.insurer_party_id, "Unknown")
        ),
        ChangeDisplayRow(
            label="Covers",
            value=", ".join(
                names.get(i, f"contact {i}") for i in payload.covered_party_ids
            ),
        ),
        ChangeDisplayRow(label="Kind", value=payload.kind),
    ]
    for label, value in (
        ("Plan", payload.name),
        ("Policy number", payload.policy_number),
        ("Member ID", payload.member_id),
        ("Group ID", payload.group_id),
        (
            "Effective",
            payload.effective_on.isoformat() if payload.effective_on else None,
        ),
        ("Renews", payload.renews_on.isoformat() if payload.renews_on else None),
    ):
        if value:
            rows.append(ChangeDisplayRow(label=label, value=value))
    if payload.premium_stream_id is not None:
        from app.services.finance.domains.planning.recurring import streams

        stream = await streams.get_recurring(
            db, payload.premium_stream_id, owner_user_id
        )
        rows.append(
            ChangeDisplayRow(label="Premium", value=stream.name if stream else "-")
        )
    rows.extend(ChangeDisplayRow(label=k, value=v) for k, v in payload.terms.items())
    if payload.note:
        rows.append(ChangeDisplayRow(label="Note", value=payload.note))
    return rows


async def claim_record_execute(
    db: AsyncSession, payload: ClaimRecordPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.insurance.service import InsuranceService

    document_id, _title = await _eob(db, payload, owner_user_id)
    claim = await InsuranceService(db).record_claim(
        owner_user_id=owner_user_id,
        document_id=document_id,
        **payload.model_dump(exclude={"paste_id", "document_id"}),
    )
    return {"claim_id": claim.id, "policy_id": claim.policy_id}


async def claim_record_describe(
    db: AsyncSession, payload: ClaimRecordPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.insurance.service import InsuranceService

    policy = await InsuranceService(db).get_policy(payload.policy_id)
    names = await _names(
        db,
        [
            i
            for i in (
                policy.insurer_party_id if policy else None,
                payload.covered_party_id,
                payload.provider_party_id,
            )
            if i is not None
        ],
    )
    rows = [
        ChangeDisplayRow(
            label="Policy",
            value=(policy.name or names.get(policy.insurer_party_id, "-"))
            if policy
            else f"policy {payload.policy_id}",
        ),
        ChangeDisplayRow(label="For", value=names.get(payload.covered_party_id, "-")),
        ChangeDisplayRow(label="Visit", value=payload.service_on.isoformat()),
    ]
    if payload.provider_party_id is not None:
        rows.append(
            ChangeDisplayRow(
                label="Provider", value=names.get(payload.provider_party_id, "-")
            )
        )
    if payload.claim_number:
        rows.append(ChangeDisplayRow(label="Claim", value=payload.claim_number))
    rows.append(ChangeDisplayRow(label="Status", value=payload.status))
    rows.extend(
        ChangeDisplayRow(label=label, value=format_usd(cents))
        for label, cents in (
            ("Billed", payload.billed_cents),
            ("Allowed", payload.allowed_cents),
            ("Insurer paid", payload.insurer_paid_cents),
            ("You owe the provider", payload.patient_owes_cents),
        )
    )
    try:
        _id, title = await _eob(db, payload, owner_user_id)
    except ValueError as exc:
        title = str(exc)
    if title:
        rows.append(ChangeDisplayRow(label="EOB", value=title))
    if payload.note:
        rows.append(ChangeDisplayRow(label="Note", value=payload.note))
    return rows


class ClaimPaidPayload(BaseModel):
    """Which charge paid which claim. The transaction comes from
    claim_candidates(), the way a bill's match comes from its shortlist."""

    model_config = ConfigDict(extra="forbid")

    claim_id: int
    transaction_id: int


async def claim_paid_execute(
    db: AsyncSession, payload: ClaimPaidPayload, owner_user_id: int | None
) -> dict[str, Any]:
    from app.services.insurance.service import InsuranceService

    claim = await InsuranceService(db).mark_paid(
        payload.claim_id, payload.transaction_id, owner_user_id=owner_user_id
    )
    return {"claim_id": claim.id, "transaction_id": payload.transaction_id}


async def claim_paid_describe(
    db: AsyncSession, payload: ClaimPaidPayload, owner_user_id: int | None
) -> list[ChangeDisplayRow]:
    from app.services.finance.domains.detection.insights.formatting import format_usd
    from app.services.finance.domains.writes.display import txn_row
    from app.services.insurance.service import InsuranceService

    claim = await InsuranceService(db).get_claim(payload.claim_id)
    _txn, payment = await txn_row(db, payload.transaction_id, owner_user_id)
    payment.label = "Payment"
    return [
        ChangeDisplayRow(
            label="Claim",
            value=f"{claim.service_on.isoformat()} · owed {format_usd(claim.patient_owes_cents)}"
            if claim
            else f"claim {payload.claim_id}",
        ),
        payment,
    ]
