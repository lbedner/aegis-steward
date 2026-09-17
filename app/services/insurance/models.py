"""The policy and the claim."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field, SQLModel

from app.core.schema import one_of

POLICY_KINDS = ("dental", "health", "vision", "auto", "home", "life", "other")

# What an insurer has said about a claim, as the EOB says it.
CLAIM_STATUSES = ("submitted", "processed", "denied", "appealed", "paid")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class InsurancePolicy(SQLModel, table=True):
    """One plan an insurer wrote for some people.

    The figures a plan states - deductible, annual maximum, waiting
    periods, coverage tiers - differ by kind: a dental plan has an
    orthodontic lifetime maximum and an auto policy has a collision
    deductible. So ``terms`` is a JSON of labelled lines, as the plan
    documents label them, and the columns are the things every policy
    has: who wrote it, who it covers, its numbers, its dates, and the
    stream its premium is paid through.
    """

    __tablename__ = "insurance_policy"
    __table_args__ = (
        CheckConstraint(one_of("kind", POLICY_KINDS), name="ck_insurance_policy_kind"),
        Index("ix_insurance_policy_owner", "owner_user_id"),
        Index("ix_insurance_policy_insurer", "insurer_party_id"),
        Index("ix_insurance_policy_stream", "premium_stream_id"),
        Index("ix_insurance_policy_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)

    insurer_party_id: int = Field()
    # The people on the plan. A JSON list rather than a join table: a
    # policy covers a handful of names, is read whole, and is never
    # queried from the person's side except by scanning the family.
    covered_party_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    kind: str = Field(max_length=16)
    # What the insurer calls it: "Delta Dental PPO Premium Plan".
    name: str | None = Field(default=None, max_length=255)
    policy_number: str | None = Field(default=None, max_length=64)
    member_id: str | None = Field(default=None, max_length=64)
    group_id: str | None = Field(default=None, max_length=64)
    effective_on: date | None = Field(default=None)
    renews_on: date | None = Field(default=None)
    # The bill the premium arrives as, once declared.
    premium_stream_id: int | None = Field(default=None)
    terms: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    note: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    deleted_at: datetime | None = Field(default=None)


class InsuranceClaim(SQLModel, table=True):
    """One visit as the insurer settled it.

    The four figures are the EOB's own: what the provider billed, what
    the plan allowed, what the insurer paid, and what the patient owes.
    The last is owed to the PROVIDER - "this is not a bill" - which is
    why it is here and not a bill stream.
    """

    __tablename__ = "insurance_claim"
    __table_args__ = (
        CheckConstraint(
            one_of("status", CLAIM_STATUSES), name="ck_insurance_claim_status"
        ),
        Index("ix_insurance_claim_policy", "policy_id"),
        Index("ix_insurance_claim_covered", "covered_party_id"),
        Index("ix_insurance_claim_provider", "provider_party_id"),
        Index("ix_insurance_claim_document", "document_id"),
        Index("ix_insurance_claim_paid", "paid_transaction_id"),
        Index("ix_insurance_claim_deleted", "deleted_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int | None = Field(default=None)

    policy_id: int = Field()
    covered_party_id: int = Field()
    service_on: date = Field()
    provider_party_id: int | None = Field(default=None)
    claim_number: str | None = Field(default=None, max_length=64)
    status: str = Field(default="processed", max_length=16)
    billed_cents: int = Field(default=0)
    allowed_cents: int = Field(default=0)
    insurer_paid_cents: int = Field(default=0)
    patient_owes_cents: int = Field(default=0)
    # The EOB, on the shelf.
    document_id: int | None = Field(default=None)
    # The charge on the ledger that paid the provider. Set, the claim
    # leaves "owed to providers"; the EOB said what was owed and the
    # ledger shows it leaving, and this is the link between them.
    paid_transaction_id: int | None = Field(default=None)
    note: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    deleted_at: datetime | None = Field(default=None)
