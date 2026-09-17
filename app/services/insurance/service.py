"""Reading and writing policies and claims."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.services.insurance.models import (
    CLAIM_STATUSES,
    POLICY_KINDS,
    InsuranceClaim,
    InsurancePolicy,
)


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

    async def policies_covering_any(self) -> list[InsurancePolicy]:
        """Every live policy, newest effective first."""
        query = (
            select(InsurancePolicy)
            .where(col(InsurancePolicy.deleted_at).is_(None))
            .order_by(col(InsurancePolicy.effective_on).desc(), col(InsurancePolicy.id))
        )
        return list((await self.db.exec(query)).all())

    async def policies_covering(self, party_id: int) -> list[InsurancePolicy]:
        """What covers this person. The covered list is JSON, so this
        scans live policies; a household has a handful."""
        return [
            p
            for p in await self.policies_covering_any()
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
        """What the EOBs on this policy say is still owed to providers,
        in cents: claims with no paying charge linked."""
        query = (
            select(func.coalesce(func.sum(InsuranceClaim.patient_owes_cents), 0))
            .where(col(InsuranceClaim.policy_id) == policy_id)
            .where(col(InsuranceClaim.paid_transaction_id).is_(None))
            .where(col(InsuranceClaim.deleted_at).is_(None))
        )
        return int((await self.db.exec(query)).one())

    async def get_claim(self, claim_id: int) -> InsuranceClaim | None:
        claim = await self.db.get(InsuranceClaim, claim_id)
        return None if claim is None or claim.deleted_at else claim

    async def claim_candidates(
        self, claim_id: int, *, owner_user_id: int | None
    ) -> list[Any]:
        """The charges that could have paid this claim: outflows within
        a month of the visit whose amount is within a tenth of what the
        EOB said was owed. The same shape as a bill's match shortlist,
        so the picker and Illiana read one list."""
        from datetime import timedelta

        from app.services.finance.models import FinanceTransaction

        claim = await self.get_claim(claim_id)
        if claim is None or not claim.patient_owes_cents:
            return []
        owed = claim.patient_owes_cents
        band = max(owed // 10, 100)
        query = (
            select(FinanceTransaction)
            .where(col(FinanceTransaction.amount) < 0)
            .where(
                col(FinanceTransaction.amount).between(-(owed + band), -(owed - band))
            )
            .where(
                col(FinanceTransaction.date_).between(
                    claim.service_on - timedelta(days=7),
                    claim.service_on + timedelta(days=45),
                )
            )
            .order_by(col(FinanceTransaction.date_).desc())
        )
        if owner_user_id is not None:
            query = query.where(col(FinanceTransaction.owner_user_id) == owner_user_id)
        return list((await self.db.exec(query)).all())

    async def mark_paid(
        self, claim_id: int, transaction_id: int, *, owner_user_id: int | None
    ) -> InsuranceClaim:
        from app.services.finance.domains.ledger.queries.transactions import (
            transaction_by_id,
        )

        claim = await self.get_claim(claim_id)
        if claim is None:
            raise ValueError(f"No claim with id {claim_id}")
        txn = await transaction_by_id(
            self.db, transaction_id, owner_user_id=owner_user_id
        )
        if txn is None:
            raise ValueError(f"Transaction {transaction_id} not found.")
        claim.paid_transaction_id = transaction_id
        claim.updated_at = utcnow()
        self.db.add(claim)
        await self.db.flush()
        return claim
