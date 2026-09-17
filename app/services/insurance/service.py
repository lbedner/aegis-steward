"""Reading and writing policies and claims."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.insurance.models import (
    CLAIM_STATUSES,
    POLICY_KINDS,
    InsuranceClaim,
    InsurancePolicy,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class InsuranceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_policy(
        self,
        *,
        insurer_party_id: int,
        covered_party_ids: list[int],
        kind: str,
        name: str | None = None,
        policy_number: str | None = None,
        member_id: str | None = None,
        group_id: str | None = None,
        effective_on: date | None = None,
        renews_on: date | None = None,
        premium_stream_id: int | None = None,
        terms: dict[str, Any] | None = None,
        note: str | None = None,
        owner_user_id: int | None = None,
    ) -> InsurancePolicy:
        if kind not in POLICY_KINDS:
            raise ValueError(f"One of: {', '.join(POLICY_KINDS)}.")
        policy = InsurancePolicy(
            owner_user_id=owner_user_id,
            insurer_party_id=insurer_party_id,
            covered_party_ids=list(covered_party_ids),
            kind=kind,
            name=(name or "").strip() or None,
            policy_number=policy_number,
            member_id=member_id,
            group_id=group_id,
            effective_on=effective_on,
            renews_on=renews_on,
            premium_stream_id=premium_stream_id,
            terms=dict(terms or {}),
            note=(note or "").strip() or None,
        )
        self.db.add(policy)
        await self.db.flush()
        return policy

    async def get_policy(self, policy_id: int) -> InsurancePolicy | None:
        policy = await self.db.get(InsurancePolicy, policy_id)
        return None if policy is None or policy.deleted_at else policy

    async def policies_of(self, insurer_party_id: int) -> list[InsurancePolicy]:
        """What this insurer wrote."""
        query = (
            select(InsurancePolicy)
            .where(col(InsurancePolicy.insurer_party_id) == insurer_party_id)
            .where(col(InsurancePolicy.deleted_at).is_(None))
            .order_by(col(InsurancePolicy.effective_on).desc(), col(InsurancePolicy.id))
        )
        return list((await self.db.exec(query)).all())

    async def policies_covering(self, party_id: int) -> list[InsurancePolicy]:
        """What covers this person. The covered list is JSON, so this
        scans live policies; a household has a handful."""
        query = (
            select(InsurancePolicy)
            .where(col(InsurancePolicy.deleted_at).is_(None))
            .order_by(col(InsurancePolicy.effective_on).desc(), col(InsurancePolicy.id))
        )
        return [
            p
            for p in (await self.db.exec(query)).all()
            if party_id in (p.covered_party_ids or [])
        ]

    async def record_claim(
        self,
        *,
        policy_id: int,
        covered_party_id: int,
        service_on: date,
        provider_party_id: int | None = None,
        claim_number: str | None = None,
        status: str = "processed",
        billed_cents: int = 0,
        allowed_cents: int = 0,
        insurer_paid_cents: int = 0,
        patient_owes_cents: int = 0,
        document_id: int | None = None,
        note: str | None = None,
        owner_user_id: int | None = None,
    ) -> InsuranceClaim:
        if status not in CLAIM_STATUSES:
            raise ValueError(f"One of: {', '.join(CLAIM_STATUSES)}.")
        if await self.get_policy(policy_id) is None:
            raise ValueError(f"No policy with id {policy_id}")
        claim = InsuranceClaim(
            owner_user_id=owner_user_id,
            policy_id=policy_id,
            covered_party_id=covered_party_id,
            service_on=service_on,
            provider_party_id=provider_party_id,
            claim_number=claim_number,
            status=status,
            billed_cents=billed_cents,
            allowed_cents=allowed_cents,
            insurer_paid_cents=insurer_paid_cents,
            patient_owes_cents=patient_owes_cents,
            document_id=document_id,
            note=(note or "").strip() or None,
        )
        self.db.add(claim)
        await self.db.flush()
        return claim

    async def claims_of(self, policy_id: int) -> list[InsuranceClaim]:
        """Newest visit first."""
        query = (
            select(InsuranceClaim)
            .where(col(InsuranceClaim.policy_id) == policy_id)
            .where(col(InsuranceClaim.deleted_at).is_(None))
            .order_by(
                col(InsuranceClaim.service_on).desc(), col(InsuranceClaim.id).desc()
            )
        )
        return list((await self.db.exec(query)).all())

    async def patient_owes_total(self, policy_id: int) -> int:
        """What the EOBs on this policy say is owed to providers, in cents."""
        query = (
            select(func.coalesce(func.sum(InsuranceClaim.patient_owes_cents), 0))
            .where(col(InsuranceClaim.policy_id) == policy_id)
            .where(col(InsuranceClaim.deleted_at).is_(None))
        )
        return int((await self.db.exec(query)).one())
