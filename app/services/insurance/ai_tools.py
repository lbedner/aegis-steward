"""The insurance surface Illiana reads: policies and the claims on them."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.db import get_async_session
from app.services.ai.domains.chat.tools import register_tool
from app.services.insurance.service import InsuranceService
from app.services.matters.service import PartyService


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


async def policies() -> dict[str, Any]:
    """Every insurance policy on file, with its claims.

    Returns a dict with key 'policies': entries carrying 'id',
    'insurer_party_id', 'insurer', 'covered_party_ids', 'kind', 'name',
    'policy_number', 'member_id', 'group_id', 'effective_on',
    'renews_on', 'premium_stream_id', 'terms' (labelled lines as the
    plan states them), and 'claims' - each with 'id', 'covered_party_id',
    'service_on', 'provider_party_id', 'claim_number', 'status', the
    four EOB figures in cents, and 'document_id' (readable with
    `paper`). 'patient_owes_cents' totals what the EOBs say is owed to
    providers. The ids are what claim.record's payload names.
    """
    async with get_async_session() as db:
        service = InsuranceService(db)
        parties = PartyService(db)
        rows = []
        for policy in await service.policies_covering_any():
            insurer = await parties.get(policy.insurer_party_id)
            claims = await service.claims_of(int(policy.id))
            rows.append(
                {
                    "id": policy.id,
                    "insurer_party_id": policy.insurer_party_id,
                    "insurer": insurer.name if insurer else None,
                    "covered_party_ids": policy.covered_party_ids,
                    "kind": policy.kind,
                    "name": policy.name,
                    "policy_number": policy.policy_number,
                    "member_id": policy.member_id,
                    "group_id": policy.group_id,
                    "effective_on": _iso(policy.effective_on),
                    "renews_on": _iso(policy.renews_on),
                    "premium_stream_id": policy.premium_stream_id,
                    "terms": policy.terms or {},
                    "patient_owes_cents": sum(c.patient_owes_cents for c in claims),
                    "claims": [
                        {
                            "id": c.id,
                            "covered_party_id": c.covered_party_id,
                            "service_on": _iso(c.service_on),
                            "provider_party_id": c.provider_party_id,
                            "claim_number": c.claim_number,
                            "status": c.status,
                            "billed_cents": c.billed_cents,
                            "allowed_cents": c.allowed_cents,
                            "insurer_paid_cents": c.insurer_paid_cents,
                            "patient_owes_cents": c.patient_owes_cents,
                            "document_id": c.document_id,
                        }
                        for c in claims
                    ],
                }
            )
    return {"policies": rows}


register_tool(
    "policies",
    policies,
    description="Insurance policies on file, their terms and their claims, with ids",
    replace=True,
)
